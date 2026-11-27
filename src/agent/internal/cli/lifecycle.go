package cli

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	goruntime "runtime"
	"strings"
	"time"

	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/platform/lifecycle"
)

func runLifecycle(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: hfl-agent lifecycle ready|check-database|begin|authorize|fail|verify-rollback|prepare-rollback")
	}
	fs := flag.NewFlagSet("lifecycle "+args[0], flag.ContinueOnError)
	configuredRoot := fs.String("data-dir", "", "Agent Root")
	version := fs.String("version", "", "expected version")
	commit := fs.String("commit", "", "expected commit")
	archive := fs.String("archive", "", "verified staged archive")
	pid := fs.Int("pid", 0, "service PID")
	id := fs.String("attempt", "", "upgrade attempt identity")
	phase := fs.String("phase", "installer_failed", "failed installer phase")
	code := fs.String("error-code", "AGENT_UPGRADE_INSTALL_FAILED", "stable failure code")
	rollback := fs.String("rollback-phase", "", "rollback transaction phase")
	exit := fs.Int("exit-code", 1, "installer exit code")
	workspace := fs.String("workspace", "", "private rollback staging workspace")
	service := fs.String("service", "none", "previous service snapshot type")
	wasActive := fs.Bool("was-active", false, "previous service was active")
	uid := fs.Int64("runtime-uid", -1, "administrator verification of a configured service UID")
	if err := fs.Parse(args[1:]); err != nil {
		return err
	}
	if fs.NArg() != 0 || *configuredRoot == "" {
		return fmt.Errorf("lifecycle requires an Agent Root and named options only")
	}
	if *uid != -1 && ((args[0] != "verify-rollback" && args[0] != "prepare-rollback") || !lifecycle.CanVerifyRuntimeOwner(*uid)) {
		return fmt.Errorf("runtime-uid is only valid for administrator rollback verification")
	}
	root, err := lifecycle.ResolveAgentRoot(*configuredRoot)
	if err != nil {
		return err
	}
	timeout := 30 * time.Second
	switch args[0] {
	case "verify-rollback", "prepare-rollback":
		timeout = rollbackVerificationTimeout
	case "check-database":
		timeout = rollbackDatabaseTimeout
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	switch args[0] {
	case "ready":
		if *pid <= 0 || int64(*pid) > 1<<31-1 || *version == "" || *commit == "" {
			return fmt.Errorf("ready requires service identity")
		}
		return lifecycle.Check(ctx, root, int32(*pid), *version, *commit)
	case "check-database":
		path, err := lifecycle.Path(root, "data/agent.db")
		if err != nil {
			return err
		}
		return database.CheckExisting(ctx, path)
	case "verify-rollback":
		err := verifyRollbackAs(ctx, root, *version, *uid)
		var verification *rollbackVerificationError
		if errors.As(err, &verification) {
			// Stdout is a fixed diagnostic code for the installer; detail stays local.
			fmt.Println(verification.code)
		}
		return err
	case "prepare-rollback":
		return prepareRollback(ctx, root, *version, *workspace, *service, *wasActive, *uid)
	case "begin", "authorize":
		// A trusted standalone upgrade uses a separate input path. The managed
		// staging slot is reserved for authenticated, currently accepted tasks.
		stagedName := "package.tar.gz"
		if goruntime.GOOS == "windows" {
			stagedName = "package.zip"
		}
		relative := filepath.Join("lifecycle", "upgrade", stagedName)
		managed, err := managedUpgradeArchive(*configuredRoot, root, relative, *archive)
		if err != nil || !managed {
			return err
		}
		path, err := lifecycle.Path(root, relative)
		if err != nil {
			return err
		}
		f, err := os.Open(path)
		if err != nil {
			return err
		}
		defer f.Close()
		hash := sha256.New()
		if _, err = io.Copy(hash, f); err != nil {
			return err
		}
		dbPath, err := lifecycle.Path(root, "data/agent.db")
		if err != nil {
			return err
		}
		db, err := database.OpenExisting(ctx, dbPath, true)
		if err != nil {
			return err
		}
		defer db.Close()
		task, err := database.NewTaskRepo(db).UpgradeCandidate(ctx, *version, *commit)
		if err != nil {
			return err
		}
		if task == nil {
			return fmt.Errorf("staged upgrade does not match an active detached task and authorized build")
		}
		if args[0] == "authorize" {
			return nil
		}
		a, err := lifecycle.Begin(root, lifecycle.Attempt{TaskID: task.ID, TaskStartedAt: task.StartedAt.UTC().Format(time.RFC3339Nano), TargetVersion: *version, TargetCommit: *commit, PackageSHA256: hex.EncodeToString(hash.Sum(nil))})
		if err != nil {
			return err
		}
		fmt.Println(a.ID)
		return nil
	case "fail":
		if *exit == 0 {
			return fmt.Errorf("failure must have a nonzero exit code")
		}
		a, err := lifecycle.Fail(root, *id, *phase, *code, *rollback, *exit)
		if err != nil {
			return err
		}
		dbPath, err := lifecycle.Path(root, "data/agent.db")
		if err != nil {
			return err
		}
		db, err := database.OpenExisting(ctx, dbPath, false)
		if err != nil {
			return err
		}
		defer db.Close()
		_, err = database.NewTaskRepo(db).ApplyUpgradeFailure(ctx, a)
		return err
	default:
		return fmt.Errorf("unsupported lifecycle operation")
	}
}

// managedUpgradeArchive compares the configured and canonical spellings first,
// so an unsafe entry at the managed location cannot fall through as standalone.
// Also recognize additional aliases of that archive as the same managed input.
func managedUpgradeArchive(configuredRoot, canonicalRoot, relative, archive string) (bool, error) {
	if archive == "" {
		return false, fmt.Errorf("missing upgrade archive path")
	}
	input, err := filepath.Abs(archive)
	if err != nil {
		return false, err
	}
	configuredSlot, err := filepath.Abs(filepath.Join(configuredRoot, relative))
	if err != nil {
		return false, err
	}
	canonicalSlot := filepath.Join(canonicalRoot, relative)
	samePath := func(a, b string) bool {
		return a == b || (goruntime.GOOS == "windows" && strings.EqualFold(a, b))
	}
	if samePath(input, configuredSlot) || samePath(input, canonicalSlot) {
		return true, nil
	}
	physical, err := filepath.EvalSymlinks(input)
	return err == nil && samePath(physical, canonicalSlot), err
}
