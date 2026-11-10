package filelock

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

func TestLockSubprocess(t *testing.T) {
	if os.Getenv("HFL_FILELOCK_CHILD") != "1" {
		return
	}
	release, err := Acquire(context.Background(), os.Getenv("HFL_FILELOCK_PATH"), true)
	if err != nil {
		t.Fatal(err)
	}
	defer release()
	fmt.Println("locked")
	_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
}

func TestMigrationLockExcludesAnotherProcess(t *testing.T) {
	bin, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "migration.lock")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, "-test.run=^TestLockSubprocess$")
	cmd.Env = append(os.Environ(), "HFL_FILELOCK_CHILD=1", "HFL_FILELOCK_PATH="+path)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		_ = stdin.Close()
		_ = cmd.Wait()
	}()
	if line, _ := bufio.NewReader(stdout).ReadString('\n'); line != "locked\n" {
		t.Fatal("child did not acquire shared lock")
	}
	deadline, stop := context.WithTimeout(ctx, 150*time.Millisecond)
	defer stop()
	if release, err := Acquire(deadline, path, false); !errors.Is(err, context.DeadlineExceeded) {
		if release != nil {
			release()
		}
		t.Fatalf("exclusive lock did not exclude other process: %v", err)
	}
}

func TestSharedUsersExcludeMigrationAndCancellationReleasesHandle(t *testing.T) {
	path := filepath.Join(t.TempDir(), "migration.lock")
	first, err := Acquire(context.Background(), path, true)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Acquire(context.Background(), path, true)
	if err != nil {
		t.Fatal(err)
	}
	first()
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()
	if unlock, err := Acquire(ctx, path, false); !errors.Is(err, context.DeadlineExceeded) {
		if unlock != nil {
			unlock()
		}
		t.Fatalf("exclusive migration ignored active user: %v", err)
	}
	second()
	unlock, err := Acquire(context.Background(), path, false)
	if err != nil {
		t.Fatal(err)
	}
	unlock()
	unlock() // Release is idempotent.
}
