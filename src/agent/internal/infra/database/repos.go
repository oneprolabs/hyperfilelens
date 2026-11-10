package database

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"hyperfilelens/agent/internal/model"
)

// ErrRepoNotFound means the repo alias is absent from agent.db.
var ErrRepoNotFound = errors.New("repo not found")

// RepoRepo persists Kopia repository aliases locally.
type RepoRepo struct {
	db *DB
}

// NewRepoRepo returns a repository registry backed by db.
func NewRepoRepo(db *DB) *RepoRepo {
	return &RepoRepo{db: db}
}

// RelocateConfigPaths switches only exact, verified migration sources. Task
// history is intentionally immutable; redelivered paths are resolved at use.
func (r *RepoRepo) RelocateConfigPaths(ctx context.Context, paths map[string]string) error {
	return r.db.WithTx(ctx, func(tx *sql.Tx) error {
		for source, target := range paths {
			if _, err := tx.ExecContext(ctx, `UPDATE repos SET config_file=? WHERE config_file=?`, target, source); err != nil {
				return err
			}
		}
		return nil
	})
}

// ConnectInput registers or updates a repo alias.
type ConnectInput struct {
	Name        string
	ConfigFile  string
	Description string
}

// Connect upserts a repo alias after validating the config file path.
func (r *RepoRepo) Connect(ctx context.Context, in ConnectInput) error {
	name := strings.TrimSpace(in.Name)
	if name == "" {
		return fmt.Errorf("empty repo name")
	}
	cfgPath := strings.TrimSpace(in.ConfigFile)
	if cfgPath == "" {
		return fmt.Errorf("empty config file")
	}
	cfgPath = filepath.Clean(cfgPath)
	if _, err := os.Stat(cfgPath); err != nil {
		return fmt.Errorf("config file: %w", err)
	}

	now := time.Now().UTC()
	_, err := r.db.conn.ExecContext(ctx, `
INSERT INTO repos (name, config_file, description, created_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(name) DO UPDATE SET
  config_file=excluded.config_file,
  description=excluded.description,
  updated_at=excluded.updated_at
`, name, cfgPath, strings.TrimSpace(in.Description), formatTime(now), formatTime(now))
	return err
}

// Disconnect removes a repo alias.
func (r *RepoRepo) Disconnect(ctx context.Context, name string) error {
	name = strings.TrimSpace(name)
	if name == "" {
		return fmt.Errorf("empty repo name")
	}
	res, err := r.db.conn.ExecContext(ctx, `DELETE FROM repos WHERE name=?`, name)
	if err != nil {
		return err
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return fmt.Errorf("%w: %s", ErrRepoNotFound, name)
	}
	return nil
}

// Get returns one repo by alias.
func (r *RepoRepo) Get(ctx context.Context, name string) (model.Repo, error) {
	name = strings.TrimSpace(name)
	if name == "" {
		return model.Repo{}, fmt.Errorf("empty repo name")
	}
	row := r.db.conn.QueryRowContext(ctx, `
SELECT name, config_file, description, created_at, updated_at
FROM repos WHERE name=?
`, name)
	repo, err := scanRepoRow(row)
	return repo, wrapRepoNotFound(name, err)
}

// List returns all registered repos ordered by name.
func (r *RepoRepo) List(ctx context.Context) ([]model.Repo, error) {
	rows, err := r.db.conn.QueryContext(ctx, `
SELECT name, config_file, description, created_at, updated_at
FROM repos ORDER BY name ASC
`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []model.Repo
	for rows.Next() {
		repo, err := scanRepoRow(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, repo)
	}
	return out, rows.Err()
}

func wrapRepoNotFound(name string, err error) error {
	if errors.Is(err, sql.ErrNoRows) {
		return fmt.Errorf("%w: %s", ErrRepoNotFound, name)
	}
	return err
}

type repoRowScanner interface {
	Scan(dest ...any) error
}

func scanRepoRow(row repoRowScanner) (model.Repo, error) {
	var (
		repo      model.Repo
		createdAt sql.NullString
		updatedAt sql.NullString
	)
	if err := row.Scan(&repo.Name, &repo.ConfigFile, &repo.Description, &createdAt, &updatedAt); err != nil {
		return model.Repo{}, err
	}
	repo.CreatedAt = parseTime(createdAt)
	repo.UpdatedAt = parseTime(updatedAt)
	return repo, nil
}
