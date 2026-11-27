package cli

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/platform/lifecycle"
)

const (
	rollbackVerificationTimeout = 4 * time.Minute
	rollbackDatabaseTimeout     = 90 * time.Second
)

type rollbackVerificationError struct {
	code string
	err  error
}

func (e *rollbackVerificationError) Error() string { return fmt.Sprintf("%s: %v", e.code, e.err) }
func (e *rollbackVerificationError) Unwrap() error { return e.err }

type rollbackContextReader struct {
	ctx context.Context
	r   io.Reader
}

func (r rollbackContextReader) Read(p []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.r.Read(p)
}

func checkRollbackDatabase(ctx context.Context, path string) error {
	ctx, cancel := context.WithTimeout(ctx, rollbackDatabaseTimeout)
	defer cancel()
	err := database.CheckExisting(ctx, path)
	if deadline := ctx.Err(); deadline != nil {
		return deadline
	}
	return err
}

func fileDigest(ctx context.Context, path string) ([32]byte, error) {
	var sum [32]byte
	f, err := os.Open(path)
	if err != nil {
		return sum, err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, rollbackContextReader{ctx, f}); err != nil {
		return sum, err
	}
	copy(sum[:], h.Sum(nil))
	return sum, nil
}

// verifyRollback is validation only: it never restores a live database or
// deletes recovery materials. Mismatches require explicit operator recovery.
func verifyRollback(ctx context.Context, root, version string) error {
	return verifyRollbackAs(ctx, root, version, -1)
}

func verifyRollbackAs(ctx context.Context, root, version string, uid int64) (retErr error) {
	verifiedPath := func(relative string) (string, error) { return lifecycle.VerificationPath(root, relative, uid) }
	stage := "AGENT_UPGRADE_ROLLBACK_FILES_UNVERIFIED"
	defer func() {
		if retErr == nil {
			return
		}
		if errors.Is(retErr, context.DeadlineExceeded) || errors.Is(ctx.Err(), context.DeadlineExceeded) {
			retErr = &rollbackVerificationError{"AGENT_UPGRADE_ROLLBACK_VERIFICATION_TIMEOUT", fmt.Errorf("%s: %w", stage, retErr)}
		} else {
			retErr = &rollbackVerificationError{stage, retErr}
		}
	}()
	if err := ctx.Err(); err != nil {
		return err
	}
	var meta struct {
		PreviousVersion string `json:"previous_version"`
	}
	if err := lifecycle.ReadForVerification(root, "backup/rollback/meta.json", uid, &meta); err != nil {
		return err
	}
	if version == "" || meta.PreviousVersion != version {
		return fmt.Errorf("rollback version metadata mismatch")
	}
	backup, err := verifiedPath("backup/rollback/bin")
	if err != nil {
		return err
	}
	count := 0
	if err = filepath.WalkDir(backup, func(path string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if d.IsDir() {
			return nil
		}
		rel, err := filepath.Rel(backup, path)
		if err != nil {
			return err
		}
		if _, err = verifiedPath(filepath.Join("backup/rollback/bin", rel)); err != nil {
			return err
		}
		target := filepath.Join("bin", rel)
		if rel == "MANIFEST.json" || rel == "INSTALLED_VERSION" {
			target = rel
		}
		current, err := verifiedPath(target)
		if err != nil {
			return err
		}
		saved, err := fileDigest(ctx, path)
		if err != nil {
			return err
		}
		actual, err := fileDigest(ctx, current)
		if err != nil {
			return err
		}
		if actual != saved {
			return fmt.Errorf("restored installation does not match rollback binaries")
		}
		count++
		return nil
	}); err != nil {
		return err
	}
	if count == 0 {
		return fmt.Errorf("rollback binaries are missing")
	}
	for _, required := range []string{"hfl-agent", "kopia", "MANIFEST.json", "INSTALLED_VERSION"} {
		path, err := verifiedPath(filepath.Join("backup/rollback/bin", required))
		if err != nil {
			return err
		}
		if _, err := os.Stat(path); err != nil {
			return err
		}
	}
	stage = "AGENT_UPGRADE_ROLLBACK_SNAPSHOT_UNVERIFIED"
	archive, err := verifiedPath("backup/rollback/latest.tar.gz")
	if err != nil {
		return err
	}
	f, err := os.Open(archive)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(rollbackContextReader{ctx, f})
	if err != nil {
		return err
	}
	defer gz.Close()
	reader := tar.NewReader(rollbackContextReader{ctx, gz})
	configSeen := false
	dbSeen := false
	// The verifier may be an administrator. Never put its extracted SQLite
	// snapshot under a directory writable by the account being inspected.
	stagingDir, err := os.MkdirTemp("", "hfl-rollback-verify-*")
	if err != nil {
		return err
	}
	defer os.Remove(stagingDir)
	snapshot, err := os.CreateTemp(stagingDir, "database-*.db")
	if err != nil {
		return err
	}
	name := snapshot.Name()
	defer os.Remove(name)
	defer snapshot.Close()
	for {
		header, err := reader.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		name := strings.TrimPrefix(header.Name, "./")
		if header.Typeflag == tar.TypeDir {
			continue
		}
		if header.Typeflag != tar.TypeReg {
			return fmt.Errorf("rollback archive contains unsafe entries")
		}
		switch name {
		case "config/agent.env", "config/config.json":
			if header.Size > 8<<20 {
				return fmt.Errorf("rollback configuration too large")
			}
			saved, err := io.ReadAll(reader)
			if err != nil {
				return err
			}
			path, err := verifiedPath(name)
			if err != nil {
				return err
			}
			current, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			if !bytes.Equal(saved, current) {
				stage = "AGENT_UPGRADE_ROLLBACK_FILES_UNVERIFIED"
				return fmt.Errorf("restored configuration does not match rollback snapshot")
			}
			if name == "config/agent.env" {
				configSeen = true
			}
		case "data/agent.db":
			if header.Size < 0 || header.Size > 8<<30 {
				return fmt.Errorf("rollback database snapshot too large")
			}
			if dbSeen {
				return fmt.Errorf("duplicate rollback database")
			}
			dbSeen = true
			if _, err := io.Copy(snapshot, reader); err != nil {
				return err
			}
		default:
			return fmt.Errorf("rollback archive has unexpected content")
		}
	}
	if _, err := io.Copy(io.Discard, rollbackContextReader{ctx, gz}); err != nil {
		return err
	}
	if !configSeen || !dbSeen {
		return fmt.Errorf("rollback configuration or database snapshot is missing")
	}
	if err = snapshot.Close(); err != nil {
		return err
	}
	stage = "AGENT_UPGRADE_ROLLBACK_LIVE_DB_UNVERIFIED"
	current, err := verifiedPath("data/agent.db")
	if err != nil {
		return err
	}
	if err = checkRollbackDatabase(ctx, current); err != nil {
		return err
	}
	stage = "AGENT_UPGRADE_ROLLBACK_SNAPSHOT_DB_UNVERIFIED"
	if err = checkRollbackDatabase(ctx, snapshot.Name()); err != nil {
		return err
	}
	stage = "AGENT_UPGRADE_ROLLBACK_REFERENCES_UNVERIFIED"
	return database.CheckRollbackReferences(ctx, current, snapshot.Name())
}
