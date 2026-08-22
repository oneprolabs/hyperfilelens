package database

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"hyperfilelens/agent/internal/platform/vfs"
	_ "modernc.org/sqlite"
)

const schemaSQL = `
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  result TEXT NOT NULL DEFAULT '{}',
  error TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  result_reported INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'websocket'
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_result_reported ON tasks(result_reported);

CREATE TABLE IF NOT EXISTS repos (
  name TEXT PRIMARY KEY,
  config_file TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
`

const migrateSourceColumnSQL = `
ALTER TABLE tasks ADD COLUMN source TEXT NOT NULL DEFAULT 'websocket';
`

// DB wraps the local SQLite store with WAL mode and transactional helpers.
type DB struct {
	conn *sql.DB
	path string
}

// DefaultPath returns the SQLite file path under the agent data directory.
func DefaultPath(dataDir string) string {
	return vfs.AgentDatabasePath(dataDir)
}

// Open initializes the SQLite database at path with WAL journaling enabled.
func Open(ctx context.Context, path string) (*DB, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}

	dsn := fmt.Sprintf("file:%s?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)", path)
	conn, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	conn.SetMaxOpenConns(1)

	db := &DB{conn: conn, path: path}
	if err := db.migrate(ctx); err != nil {
		_ = conn.Close()
		return nil, err
	}
	return db, nil
}

func (d *DB) migrate(ctx context.Context) error {
	if _, err := d.conn.ExecContext(ctx, schemaSQL); err != nil {
		return err
	}
	_, err := d.conn.ExecContext(ctx, migrateSourceColumnSQL)
	if err != nil && !isDuplicateColumn(err) {
		return err
	}
	return nil
}

func isDuplicateColumn(err error) bool {
	if err == nil {
		return false
	}
	msg := err.Error()
	return strings.Contains(msg, "duplicate column") || strings.Contains(msg, "already exists")
}

// Path returns the database file path.
func (d *DB) Path() string { return d.path }

// Close releases the underlying database connection.
func (d *DB) Close() error {
	if d.conn == nil {
		return nil
	}
	return d.conn.Close()
}

// WithTx runs fn inside a SQLite transaction.
func (d *DB) WithTx(ctx context.Context, fn func(*sql.Tx) error) error {
	tx, err := d.conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	if err := fn(tx); err != nil {
		_ = tx.Rollback()
		return err
	}
	return tx.Commit()
}

func formatTime(t time.Time) string {
	return t.UTC().Format(time.RFC3339Nano)
}

func parseTime(raw sql.NullString) *time.Time {
	if !raw.Valid || raw.String == "" {
		return nil
	}
	t, err := time.Parse(time.RFC3339Nano, raw.String)
	if err != nil {
		return nil
	}
	utc := t.UTC()
	return &utc
}
