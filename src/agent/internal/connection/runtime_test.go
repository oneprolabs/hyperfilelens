//go:build !windows

package connection

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func composeFixture(t *testing.T, plugin, standalone string) (string, string) {
	t.Helper()
	bin, root := t.TempDir(), t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "runtime", "lensnode"), 0700); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(root, "commands.log")
	t.Setenv("PATH", bin)
	t.Setenv("TEST_COMPOSE_LOG", log)
	t.Setenv("TEST_COMPOSE_PLUGIN_VERSION", plugin)
	t.Setenv("TEST_COMPOSE_STANDALONE_VERSION", standalone)
	t.Setenv("TEST_COMPOSE_FAIL", "")
	for name, script := range map[string]string{
		"docker": `#!/bin/sh
if [ "$1" != compose ]; then exit 1; fi
shift
if [ "$1" = version ]; then
  [ -n "$TEST_COMPOSE_PLUGIN_VERSION" ] || exit 1
  printf '%s\n' "$TEST_COMPOSE_PLUGIN_VERSION"
  exit 0
fi
printf 'plugin:%s\n' "$*" >>"$TEST_COMPOSE_LOG"
[ -z "$TEST_COMPOSE_FAIL" ] || exit 1
`,
		"docker-compose": `#!/bin/sh
if [ "$1" = version ]; then
  [ -n "$TEST_COMPOSE_STANDALONE_VERSION" ] || exit 1
  printf '%s\n' "$TEST_COMPOSE_STANDALONE_VERSION"
  exit 0
fi
printf 'standalone:%s\n' "$*" >>"$TEST_COMPOSE_LOG"
[ -z "$TEST_COMPOSE_FAIL" ] || exit 1
`,
	} {
		if err := os.WriteFile(filepath.Join(bin, name), []byte(script), 0700); err != nil {
			t.Fatal(err)
		}
	}
	return root, log
}

func TestComposeSelectionMatchesSupportedGatewayInstallations(t *testing.T) {
	for _, tc := range []struct{ plugin, standalone, selected string }{
		{"v2.20.0", "v2.24.0", "plugin:"},
		{"", "Docker Compose version v2.20.0", "standalone:"},
		{"v2.19.0", "v2.24.0", "standalone:"},
	} {
		t.Run(tc.plugin+"/"+tc.standalone, func(t *testing.T) {
			root, log := composeFixture(t, tc.plugin, tc.standalone)
			c := &composeRuntime{}
			if err := c.applyLens(context.Background(), root, false); err != nil {
				t.Fatal(err)
			}
			raw, err := os.ReadFile(log)
			if err != nil || strings.Count(string(raw), tc.selected) != 2 ||
				!strings.Contains(string(raw), "--no-start --no-deps --force-recreate --pull never --no-build lensnode") {
				t.Fatal("incorrect selected implementation or stopped-state command", string(raw), err)
			}
		})
	}
}

func TestComposeSelectionIsPinnedAndNeverRetriesMutation(t *testing.T) {
	root, log := composeFixture(t, "", "v2.24.0")
	c := &composeRuntime{}
	if err := c.resolve(context.Background()); err != nil {
		t.Fatal(err)
	}
	t.Setenv("TEST_COMPOSE_PLUGIN_VERSION", "v2.30.0")
	if err := c.stopLens(context.Background(), root); err != nil {
		t.Fatal(err)
	}
	t.Setenv("TEST_COMPOSE_FAIL", "yes")
	if err := c.stopLens(context.Background(), root); err == nil {
		t.Fatal("expected operation failure")
	}
	raw, err := os.ReadFile(log)
	if err != nil || strings.Count(string(raw), "standalone:") != 2 || strings.Contains(string(raw), "plugin:") {
		t.Fatal("operation reselected Compose or retried a mutation", string(raw), err)
	}
}

func TestComposeRejectsUnsupportedVersionsBeforeMutation(t *testing.T) {
	root, log := composeFixture(t, "v2.19.9", "1.29.2")
	if err := (&composeRuntime{}).applyLens(context.Background(), root, true); err == nil {
		t.Fatal("accepted unsupported Compose versions")
	}
	if _, err := os.Stat(log); !os.IsNotExist(err) {
		t.Fatal("unsupported implementation ran a mutating command", err)
	}
}

func TestWaitStoppedChecksActualStateAndTimesOut(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := waitStopped(ctx, func() (bool, error) { return true, nil }); err == nil {
		t.Fatal("accepted an unconfirmed stop")
	}
	if err := waitStopped(context.Background(), func() (bool, error) { return false, nil }); err != nil {
		t.Fatal(err)
	}
}
