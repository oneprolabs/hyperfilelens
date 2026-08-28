package database

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
)

// Backup creates a transactionally consistent, standalone SQLite database.
// SQLite's VACUUM INTO reads one database snapshot even while the Agent keeps
// writing in WAL mode, so installers do not need to race-copy db/wal/shm files.
func Backup(ctx context.Context, source, destination string) (retErr error) {
	source = filepath.Clean(source)
	destination = filepath.Clean(destination)
	if source == destination {
		return fmt.Errorf("database backup source and destination must differ")
	}
	info, err := os.Stat(source)
	if err != nil {
		return fmt.Errorf("stat source database: %w", err)
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("source database is not a regular file: %s", source)
	}
	if _, err := os.Stat(destination); err == nil {
		return fmt.Errorf("database backup destination already exists: %s", destination)
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("inspect database backup destination: %w", err)
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
		return fmt.Errorf("create database backup directory: %w", err)
	}

	dsn := fmt.Sprintf("file:%s?mode=ro&_pragma=busy_timeout(5000)", source)
	conn, err := sql.Open("sqlite", dsn)
	if err != nil {
		return fmt.Errorf("open source database: %w", err)
	}
	conn.SetMaxOpenConns(1)
	defer func() {
		if closeErr := conn.Close(); retErr == nil && closeErr != nil {
			retErr = fmt.Errorf("close source database: %w", closeErr)
		}
		if retErr != nil {
			_ = os.Remove(destination)
		}
	}()

	if _, err := conn.ExecContext(ctx, "VACUUM INTO ?", destination); err != nil {
		return fmt.Errorf("create consistent database backup: %w", err)
	}
	if err := verifyBackup(ctx, destination); err != nil {
		return err
	}
	return nil
}

func verifyBackup(ctx context.Context, path string) error {
	conn, err := sql.Open("sqlite", fmt.Sprintf("file:%s?mode=ro", path))
	if err != nil {
		return fmt.Errorf("open database backup for verification: %w", err)
	}
	defer conn.Close()
	var result string
	if err := conn.QueryRowContext(ctx, "PRAGMA quick_check").Scan(&result); err != nil {
		return fmt.Errorf("verify database backup: %w", err)
	}
	if result != "ok" {
		return fmt.Errorf("verify database backup: quick_check returned %q", result)
	}
	return nil
}
