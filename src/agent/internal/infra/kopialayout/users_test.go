package kopialayout

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestEnvironmentOnlyAgentChild(t *testing.T) {
	if os.Getenv("HFL_LAYOUT_USER_HELPER") != "1" {
		return
	}
	fmt.Println("ready")
	_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
}

func TestCheckUsersDetectsExternallyLaunchedEnvironmentOnlyAgent(t *testing.T) {
	root := t.TempDir()
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	name := "hfl-agent"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	path := filepath.Join(t.TempDir(), name)
	source, err := os.Open(executable)
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	target, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o700)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := io.Copy(target, source); err != nil {
		_ = target.Close()
		t.Fatal(err)
	}
	if err := target.Close(); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, path, "-test.run=^TestEnvironmentOnlyAgentChild$")
	cmd.Env = append(os.Environ(), "HFL_LAYOUT_USER_HELPER=1", "HFL_AGENT_HOME="+root)
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
	if line, _ := bufio.NewReader(stdout).ReadString('\n'); line != "ready\n" {
		t.Fatal("external Agent helper did not start")
	}
	if err := CheckUsers(ctx, root, false); err == nil || !strings.Contains(err.Error(), "CACHE_LAYOUT_BUSY") {
		t.Fatalf("environment-only Agent was not excluded from migration: %v", err)
	}
	if err := CheckUsers(ctx, root, true); err != nil {
		t.Fatalf("upgrade preflight must allow the coordinating Agent: %v", err)
	}
}
