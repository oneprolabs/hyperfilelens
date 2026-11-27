//go:build !windows

package kopialayout

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	gprocess "github.com/shirou/gopsutil/v4/process"
)

func TestMain(m *testing.M) {
	// A real hfl-agent-named process with arbitrary CLI arguments provides a
	// deterministic process-level regression without opening a live repository.
	if os.Getenv("HFL_LAYOUT_PROBE_HELPER") == "1" {
		fmt.Println("ready")
		_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
		os.Exit(0)
	}
	os.Exit(m.Run())
}

func TestCheckUsersDistinguishesHealthProbeFromActualAgent(t *testing.T) {
	root := t.TempDir()
	bin := filepath.Join(root, "bin")
	if err := os.Mkdir(bin, 0700); err != nil {
		t.Fatal(err)
	}
	current, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	source, err := os.Open(current)
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	executable := filepath.Join(bin, "hfl-agent")
	dest, err := os.OpenFile(executable, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0700)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := io.Copy(dest, source); err != nil {
		t.Fatal(err)
	}
	if err := dest.Close(); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name   string
		args   []string
		busy   bool
		exited bool
	}{
		{"installer database CLI still blocks migration", []string{"tasks", "list", "--data-dir", root}, true, false},
		{"readiness probe does not block migration", []string{"lifecycle", "ready", "--data-dir", root}, false, false},
		{"another daemon still blocks migration", []string{"run", "--data-dir", root}, true, false},
		{"exited daemon is not an active user", []string{"run", "--data-dir", root}, false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cmd := exec.Command(executable, tc.args...)
			cmd.Env = append(os.Environ(), "HFL_LAYOUT_PROBE_HELPER=1")
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
			defer func() { _ = stdin.Close(); _ = cmd.Wait() }()
			line, err := bufio.NewReader(stdout).ReadString('\n')
			if err != nil || line != "ready\n" {
				t.Fatalf("helper did not start: %q %v", line, err)
			}
			if tc.exited {
				_ = stdin.Close()
				proc, err := gprocess.NewProcess(int32(cmd.Process.Pid))
				if err != nil {
					t.Fatal(err)
				}
				zombie := false
				deadline := time.Now().Add(2 * time.Second)
				for time.Now().Before(deadline) {
					states, _ := proc.Status()
					for _, state := range states {
						if state == gprocess.Zombie {
							zombie = true
						}
					}
					if zombie {
						break
					}
					time.Sleep(10 * time.Millisecond)
				}
				if !zombie {
					t.Fatal("helper did not reach unreaped exit state")
				}
			}
			err = CheckUsers(context.Background(), root, false)
			if tc.busy {
				if err == nil || !strings.Contains(err.Error(), "CACHE_LAYOUT_BUSY") || !strings.Contains(err.Error(), "pid=") {
					t.Fatalf("missing safe blocker diagnostic: %v", err)
				}
			} else if err != nil {
				t.Fatalf("readiness probe rejected: %v", err)
			}
		})
	}
}
