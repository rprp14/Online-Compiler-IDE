import argparse
from collections import defaultdict

from app import (
    OBJECT_STORAGE_DIR,
    DATABASE_PATH,
    app,
    file_storage_key,
    get_db_connection,
    normalized_parent_key,
    storage_client,
)


def _dedupe_groups(rows, key_fn):
    groups = defaultdict(list)
    for row in rows:
        groups[key_fn(row)].append(row)
    return {
        key: sorted(items, key=lambda item: item["id"], reverse=True)
        for key, items in groups.items()
        if len(items) > 1
    }


def _delete_storage_object(key: str):
    if not key:
        return
    if storage_client.mode == "s3" and storage_client.client:
        storage_client.client.delete_object(Bucket=storage_client.bucket, Key=key)
        return
    target = OBJECT_STORAGE_DIR / key
    if target.exists():
        target.unlink()


def _cleanup_folder_duplicates(connection, dry_run: bool):
    folder_rows = connection.execute(
        """
        SELECT id, user_id, parent_id, name
        FROM workspace_folders
        WHERE is_deleted = 0
        """
    ).fetchall()
    duplicates = _dedupe_groups(
        folder_rows,
        lambda row: (row["user_id"], normalized_parent_key(row["parent_id"]), row["name"].lower()),
    )
    removed = 0

    for key, items in duplicates.items():
        keeper = items[0]
        losers = items[1:]
        print(f"[folder] keep #{keeper['id']} {keeper['name']!r} for group {key}; remove {[row['id'] for row in losers]}")
        for loser in losers:
            if dry_run:
                continue
            connection.execute(
                "UPDATE workspace_folders SET parent_id = ? WHERE parent_id = ?",
                (keeper["id"], loser["id"]),
            )
            connection.execute(
                "UPDATE code_files SET folder_id = ? WHERE folder_id = ?",
                (keeper["id"], loser["id"]),
            )
            connection.execute(
                "UPDATE workspace_shares SET resource_id = ? WHERE resource_type = 'folder' AND resource_id = ?",
                (keeper["id"], loser["id"]),
            )
            connection.execute("DELETE FROM workspace_folders WHERE id = ?", (loser["id"],))
            removed += 1

    return removed


def _cleanup_file_duplicates(connection, dry_run: bool):
    file_rows = connection.execute(
        """
        SELECT id, user_id, folder_id, filename, storage_key
        FROM code_files
        WHERE is_deleted = 0
        """
    ).fetchall()
    duplicates = _dedupe_groups(
        file_rows,
        lambda row: (row["user_id"], normalized_parent_key(row["folder_id"]), row["filename"].lower()),
    )
    removed = 0

    for key, items in duplicates.items():
        keeper = items[0]
        losers = items[1:]
        print(f"[file] keep #{keeper['id']} {keeper['filename']!r} for group {key}; remove {[row['id'] for row in losers]}")
        for loser in losers:
            if dry_run:
                continue

            connection.execute(
                "UPDATE workspace_state SET selected_file_id = ? WHERE selected_file_id = ?",
                (keeper["id"], loser["id"]),
            )
            connection.execute(
                "UPDATE workspace_shares SET resource_id = ? WHERE resource_type = 'file' AND resource_id = ?",
                (keeper["id"], loser["id"]),
            )
            connection.execute(
                "UPDATE workspace_links SET file_id = ? WHERE file_id = ?",
                (keeper["id"], loser["id"]),
            )
            connection.execute(
                "UPDATE deployments SET file_id = ? WHERE file_id = ?",
                (keeper["id"], loser["id"]),
            )
            connection.execute(
                "UPDATE execution_history SET file_id = ? WHERE file_id = ?",
                (keeper["id"], loser["id"]),
            )

            keeper_max = connection.execute(
                "SELECT COALESCE(MAX(version_number), 0) AS max_version FROM code_versions WHERE file_id = ?",
                (keeper["id"],),
            ).fetchone()["max_version"]
            loser_versions = connection.execute(
                """
                SELECT id, version_number, code, storage_key
                FROM code_versions
                WHERE file_id = ?
                ORDER BY version_number ASC, id ASC
                """,
                (loser["id"],),
            ).fetchall()
            for offset, version in enumerate(loser_versions, start=1):
                new_version_number = keeper_max + offset
                new_storage_key = None
                if version["storage_key"]:
                    new_storage_key = file_storage_key(keeper["user_id"], keeper["id"], new_version_number)
                    content = storage_client.read_text(version["storage_key"])
                    storage_client.write_text(new_storage_key, content)
                    _delete_storage_object(version["storage_key"])
                connection.execute(
                    "UPDATE code_versions SET file_id = ?, version_number = ?, storage_key = ? WHERE id = ?",
                    (keeper["id"], new_version_number, new_storage_key, version["id"]),
                )

            connection.execute("DELETE FROM code_files WHERE id = ?", (loser["id"],))
            removed += 1

    return removed


def cleanup_duplicates(dry_run: bool = True):
    connection = get_db_connection()
    total_removed = 0
    try:
        # Duplicate folders can create duplicate files after re-parenting, so run a
        # few passes until the tree is stable.
        for _ in range(5):
            folder_removed = _cleanup_folder_duplicates(connection, dry_run)
            file_removed = _cleanup_file_duplicates(connection, dry_run)
            if dry_run:
                if folder_removed == 0 and file_removed == 0:
                    break
            else:
                connection.commit()
                total_removed += folder_removed + file_removed
                if folder_removed == 0 and file_removed == 0:
                    break
        if dry_run:
            connection.rollback()
    finally:
        connection.close()
    return total_removed


def main():
    parser = argparse.ArgumentParser(description="Cloud IDE maintenance commands")
    subparsers = parser.add_subparsers(dest="command")

    cleanup_parser = subparsers.add_parser("cleanup-duplicates", help="Remove duplicate folders and files")
    cleanup_parser.add_argument("--apply", action="store_true", help="Write changes to the database")

    runserver_parser = subparsers.add_parser("runserver", help="Start the Flask app")
    _ = runserver_parser

    args = parser.parse_args()
    command = args.command or "runserver"

    if command == "runserver":
        app.run(host="127.0.0.1", port=5000, debug=False)
        return

    if command == "cleanup-duplicates":
        dry_run = not args.apply
        print(f"Database: {DATABASE_PATH}")
        print("Mode:", "dry-run" if dry_run else "apply")
        removed = cleanup_duplicates(dry_run=dry_run)
        if dry_run:
            print("Dry run complete. Re-run with --apply to delete duplicates.")
        else:
            print(f"Cleanup complete. Removed {removed} duplicate rows.")
        return

    print(f"Unsupported command: {command}")
    print("Use: python backend/manage.py runserver")
    print("Or:  python backend/manage.py cleanup-duplicates --apply")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
