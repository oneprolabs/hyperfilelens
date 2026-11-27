package database

import (
	"context"
	"database/sql"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
)

// OpenExisting does not create a database, change journal mode, or run schema
// migrations. Installer probes and old-Agent failure bridges must use this path.
func OpenExisting(ctx context.Context, path string, readOnly bool) (*DB, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, fmt.Errorf("database must be a regular file")
	}
	mode := "rw"
	if readOnly {
		mode = "ro"
	}
	uriPath := filepath.ToSlash(path)
	if filepath.VolumeName(path) != "" {
		uriPath = "/" + uriPath
	}
	u := url.URL{Scheme: "file", Path: uriPath}
	conn, err := sql.Open("sqlite", u.String()+"?mode="+mode+"&_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, err
	}
	conn.SetMaxOpenConns(1)
	if err = conn.PingContext(ctx); err != nil {
		_ = conn.Close()
		return nil, err
	}
	return &DB{conn: conn, path: path}, nil
}

// CheckExisting performs a read-only integrity check without initialization.
func CheckExisting(ctx context.Context, path string) error {
	db, err := OpenExisting(ctx, path, true)
	if err != nil {
		return err
	}
	defer db.Close()
	var value string
	if err = db.conn.QueryRowContext(ctx, "PRAGMA quick_check").Scan(&value); err != nil {
		return err
	}
	if value != "ok" {
		return fmt.Errorf("database integrity check failed")
	}
	return nil
}

// CheckRollbackReferences compares durable repository aliases, not disposable
// task/metric state that legitimately changes after the restored Agent starts.
func CheckRollbackReferences(ctx context.Context, current, snapshot string) error {
	read := func(path string) (map[string]string, error) {
		db, err := OpenExisting(ctx, path, true)
		if err != nil {
			return nil, err
		}
		defer db.Close()
		rows, err := db.conn.QueryContext(ctx, "SELECT name,config_file FROM repos")
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		refs := map[string]string{}
		for rows.Next() {
			var name, path string
			if err := rows.Scan(&name, &path); err != nil {
				return nil, err
			}
			refs[name] = path
		}
		return refs, rows.Err()
	}
	a, err := read(current)
	if err != nil {
		return err
	}
	b, err := read(snapshot)
	if err != nil {
		return err
	}
	if len(a) != len(b) {
		return fmt.Errorf("restored repository aliases differ from rollback snapshot")
	}
	for name, path := range b {
		if a[name] != path {
			return fmt.Errorf("restored repository aliases differ from rollback snapshot")
		}
	}
	return nil
}

// CheckUpgradeSnapshotSchema rejects valid SQLite files that are not Agent stores.
func CheckUpgradeSnapshotSchema(ctx context.Context, path string) error {
	db, err := OpenExisting(ctx, path, true)
	if err != nil {
		return err
	}
	defer db.Close()
	for _, query := range []string{
		"SELECT id, kind, status, payload, result FROM tasks LIMIT 0",
		"SELECT name, config_file FROM repos LIMIT 0",
	} {
		rows, err := db.conn.QueryContext(ctx, query)
		if err != nil {
			return fmt.Errorf("rollback database is not an Agent task store: %w", err)
		}
		rows.Close()
	}
	return nil
}
