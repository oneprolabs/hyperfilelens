package engine

import (
	"context"
	"errors"
	"slices"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"hyperfilelens/agent/internal/platform/process"
)

func balancedManagedPolicyPayload() map[string]any {
	return map[string]any{
		"file_filter": map[string]any{
			"configured":               true,
			"ignore_patterns":          []any{"*.tmp", "**/node_modules/**"},
			"large_file_limit_enabled": true,
			"large_file_bytes_max":     float64(524288000),
			"ignore_cache_directories": true,
			"current_filesystem_only":  false,
		},
		"backup_policy": map[string]any{
			"advanced_settings": map[string]any{
				"enabled":                             true,
				"skip_unreadable_directories":         true,
				"skip_unreadable_files":               true,
				"skip_unsupported_filesystem_entries": false,
			},
		},
		"compression": map[string]any{
			"level":                   "balanced",
			"compressor":              "zstd",
			"minimum_file_size_bytes": float64(4096),
			"maximum_file_size_bytes": float64(0),
			"only_extensions":         []any{},
			"never_extensions":        []any{".ZIP", ".jpg", ".zip"},
		},
	}
}

func TestParseManagedBackupPolicyMapsAllGroups(t *testing.T) {
	spec, err := parseManagedBackupPolicy(balancedManagedPolicyPayload())
	if err != nil {
		t.Fatalf("parseManagedBackupPolicy() error = %v", err)
	}
	if spec.LargeFileBytesMax != 524288000 || !spec.IgnoreCacheDirectories {
		t.Fatalf("unexpected file filter spec: %#v", spec)
	}
	if !spec.IgnoreDirectoryErrors || !spec.IgnoreFileErrors || spec.IgnoreUnknownTypes {
		t.Fatalf("unexpected advanced settings: %#v", spec)
	}
	if spec.CompressionLevel != "balanced" || spec.Compressor != "zstd" {
		t.Fatalf("unexpected compression spec: %#v", spec)
	}
	if !slices.Equal(spec.NeverExtensions, []string{".zip", ".jpg"}) {
		t.Fatalf("unexpected normalized extensions: %#v", spec.NeverExtensions)
	}
}

func TestManagedBackupPolicyUsesSeparateResetAndApplyArgs(t *testing.T) {
	spec, err := parseManagedBackupPolicy(balancedManagedPolicyPayload())
	if err != nil {
		t.Fatal(err)
	}
	resetArgs := managedBackupPolicyResetArgs("/tmp/repo.config", "/data/projects", true)
	if !slices.Contains(resetArgs, "--clear-ignore") || !slices.Contains(resetArgs, "--clear-never-compress") || !slices.Contains(resetArgs, "--disable-dot-ignore") {
		t.Fatalf("expected list resets: %#v", resetArgs)
	}
	if slices.Contains(resetArgs, "--add-ignore=*.tmp") {
		t.Fatalf("reset args must not add entries: %#v", resetArgs)
	}

	applyArgs := managedBackupPolicyApplyArgs("/tmp/repo.config", "/data/projects", spec)
	for _, expected := range []string{
		"--keep-latest=0",
		"--keep-hourly=0",
		"--keep-daily=0",
		"--keep-weekly=0",
		"--keep-monthly=0",
		"--keep-annual=0",
		"--ignore-identical-snapshots=false",
		"--add-ignore=*.tmp",
		"--max-file-size=524288000",
		"--ignore-file-errors=true",
		"--ignore-dir-errors=true",
		"--ignore-unknown-types=false",
		"--compression=zstd",
		"--compression-min-size=4096",
		"--compression-max-size=0",
		"--add-never-compress=.zip",
	} {
		if !slices.Contains(applyArgs, expected) {
			t.Fatalf("expected %q in apply args: %#v", expected, applyArgs)
		}
	}
	if slices.Contains(applyArgs, "--clear-ignore") || slices.Contains(applyArgs, "--clear-never-compress") {
		t.Fatalf("apply args must not clear lists: %#v", applyArgs)
	}
	if got := applyArgs[len(applyArgs)-1]; got != "/data/projects" {
		t.Fatalf("expected source path target, got %q", got)
	}
	ordinaryResetArgs := managedBackupPolicyResetArgs("/tmp/repo.config", "/data/projects", false)
	if slices.Contains(ordinaryResetArgs, "--disable-dot-ignore") {
		t.Fatalf("ordinary managed backup must preserve dot-ignore behavior: %#v", ordinaryResetArgs)
	}
	if !slices.Contains(ordinaryResetArgs, "--enable-dot-ignore") {
		t.Fatalf("ordinary managed backup must restore dot-ignore inheritance: %#v", ordinaryResetArgs)
	}
}

func TestManagedBackupPolicyKeepsSystemIgnoreAfterUserRules(t *testing.T) {
	spec, err := parseManagedBackupPolicy(balancedManagedPolicyPayload())
	if err != nil {
		t.Fatal(err)
	}
	args := managedBackupPolicyApplyArgs(
		"/tmp/repo.config",
		"/",
		spec,
		[]string{"opt/hyperfilelens-agent"},
	)
	var ignores []string
	for _, arg := range args {
		if strings.HasPrefix(arg, "--add-ignore=") {
			ignores = append(ignores, strings.TrimPrefix(arg, "--add-ignore="))
		}
	}
	if len(ignores) == 0 || ignores[len(ignores)-1] != "opt/hyperfilelens-agent" {
		t.Fatalf("system boundary must be the last ignore rule: %#v", ignores)
	}
	if strings.HasSuffix(ignores[len(ignores)-1], "/**") {
		t.Fatalf("system boundary must exclude the directory entry itself: %#v", ignores)
	}
}

func TestManagedBackupPolicyKeepsProtectedRuleAfterUserDuplicate(t *testing.T) {
	spec, err := parseManagedBackupPolicy(balancedManagedPolicyPayload())
	if err != nil {
		t.Fatal(err)
	}
	// The control-plane payload may contain the same protected rule. It must
	// not consume the mandatory final copy during de-duplication.
	spec.IgnorePatterns = []string{"opt/hyperfilelens-agent", "!opt/hyperfilelens-agent"}
	args := managedBackupPolicyApplyArgs(
		"/tmp/repo.config",
		"/",
		spec,
		[]string{"opt/hyperfilelens-agent"},
	)
	var ignores []string
	for _, arg := range args {
		if strings.HasPrefix(arg, "--add-ignore=") {
			ignores = append(ignores, strings.TrimPrefix(arg, "--add-ignore="))
		}
	}
	if len(ignores) < 2 || ignores[len(ignores)-1] != "opt/hyperfilelens-agent" || ignores[len(ignores)-2] != "!opt/hyperfilelens-agent" {
		t.Fatalf("protected rule must remain the final rule after user duplicates: %#v", ignores)
	}
}

func TestManagedBackupPolicyPreservesUserExceptionsWithSystemBoundary(t *testing.T) {
	payload := balancedManagedPolicyPayload()
	payload["file_filter"].(map[string]any)["ignore_patterns"] = []any{
		"*.tmp",
		"!important.tmp",
	}
	spec, err := parseManagedBackupPolicy(payload)
	if err != nil {
		t.Fatal(err)
	}
	original := runManagedPolicyCommand
	t.Cleanup(func() { runManagedPolicyCommand = original })
	var calls [][]string
	runManagedPolicyCommand = func(
		_ context.Context,
		_ time.Duration,
		_ string,
		args []string,
		_ map[string]string,
		_ string,
	) (process.Result, error) {
		calls = append(calls, slices.Clone(args))
		return process.Result{}, nil
	}

	_, err = applyManagedBackupPolicy(
		context.Background(),
		"kopia",
		"/tmp/repo.config",
		nil,
		"/",
		spec,
		[]string{"opt/hyperfilelens-agent"},
	)
	if err != nil {
		t.Fatalf("expected policy apply to preserve supported user exceptions: %v", err)
	}
	if len(calls) != 2 {
		t.Fatalf("expected reset and apply commands, calls=%d", len(calls))
	}
	if !slices.Contains(calls[1], "--add-ignore=opt/hyperfilelens-agent") ||
		!slices.Contains(calls[1], "--add-ignore=!important.tmp") {
		t.Fatalf("system and user rules must both be applied: %#v", calls[1])
	}
}

func TestVerifyManagedBackupProtectionRequiresEffectiveKopiaPolicy(t *testing.T) {
	original := runManagedPolicyCommand
	t.Cleanup(func() { runManagedPolicyCommand = original })

	t.Run("present", func(t *testing.T) {
		runManagedPolicyCommand = func(
			context.Context,
			time.Duration,
			string,
			[]string,
			map[string]string,
			string,
		) (process.Result, error) {
			return process.Result{Stdout: `{"files":{"ignore":["*.tmp","opt/hyperfilelens-agent"],"noParentDotFiles":true}}`}, nil
		}
		result, err := verifyManagedBackupProtection(
			context.Background(), "kopia", "/tmp/repo.config", nil, "/", []string{"opt/hyperfilelens-agent"},
		)
		if err != nil || result["system_ignore_policy_verified"] != true {
			t.Fatalf("expected verified protection, result=%#v err=%v", result, err)
		}
	})

	t.Run("missing", func(t *testing.T) {
		runManagedPolicyCommand = func(
			context.Context,
			time.Duration,
			string,
			[]string,
			map[string]string,
			string,
		) (process.Result, error) {
			return process.Result{Stdout: `{"files":{"ignore":["*.tmp"]}}`}, nil
		}
		result, err := verifyManagedBackupProtection(
			context.Background(), "kopia", "/tmp/repo.config", nil, "/", []string{"opt/hyperfilelens-agent"},
		)
		if err == nil || result["error_code"] != "BACKUP_PROTECTION_POLICY_MISSING" {
			t.Fatalf("expected missing protection failure, result=%#v err=%v", result, err)
		}
	})

	t.Run("dot-ignore-enabled", func(t *testing.T) {
		runManagedPolicyCommand = func(
			context.Context,
			time.Duration,
			string,
			[]string,
			map[string]string,
			string,
		) (process.Result, error) {
			return process.Result{Stdout: `{"files":{"ignore":["opt/hyperfilelens-agent"]}}`}, nil
		}
		result, err := verifyManagedBackupProtection(
			context.Background(), "kopia", "/tmp/repo.config", nil, "/", []string{"opt/hyperfilelens-agent"},
		)
		if err == nil || result["error_code"] != "BACKUP_PROTECTION_DOT_IGNORE_ENABLED" {
			t.Fatalf("expected dot-ignore protection failure, result=%#v err=%v", result, err)
		}
	})
}

func TestManagedBackupPolicyNoneClearsCompression(t *testing.T) {
	payload := balancedManagedPolicyPayload()
	payload["compression"] = map[string]any{
		"level":                   "none",
		"compressor":              "none",
		"minimum_file_size_bytes": float64(0),
		"maximum_file_size_bytes": float64(0),
		"only_extensions":         []any{},
		"never_extensions":        []any{},
	}
	spec, err := parseManagedBackupPolicy(payload)
	if err != nil {
		t.Fatal(err)
	}
	args := managedBackupPolicyApplyArgs("/tmp/repo.config", "/data", spec)
	if !slices.Contains(args, "--compression=none") || !slices.Contains(args, "--compression-min-size=0") {
		t.Fatalf("expected disabled compression args: %#v", args)
	}
	for _, arg := range args {
		if len(arg) >= len("--add-never-compress=") && arg[:len("--add-never-compress=")] == "--add-never-compress=" {
			t.Fatalf("none must not add never-compress entries: %#v", args)
		}
	}
}

func TestParseManagedBackupPolicyUsesControlPlaneCompressionProfile(t *testing.T) {
	payload := balancedManagedPolicyPayload()
	payload["compression"].(map[string]any)["compressor"] = "zstd-better-compression"
	payload["compression"].(map[string]any)["minimum_file_size_bytes"] = float64(8192)
	spec, err := parseManagedBackupPolicy(payload)
	if err != nil {
		t.Fatalf("expected resolved control-plane profile to pass: %v", err)
	}
	args := managedBackupPolicyApplyArgs("/tmp/repo.config", "/data", spec)
	for _, expected := range []string{
		"--compression=zstd-better-compression",
		"--compression-min-size=8192",
	} {
		if !slices.Contains(args, expected) {
			t.Fatalf("expected %q in resolved profile args: %#v", expected, args)
		}
	}
}

func TestParseManagedBackupPolicyRejectsUnknownCompressionLevel(t *testing.T) {
	payload := balancedManagedPolicyPayload()
	payload["compression"].(map[string]any)["level"] = "archive"
	if _, err := parseManagedBackupPolicy(payload); err == nil {
		t.Fatal("expected unknown compression level to fail")
	}
}

func TestParseManagedBackupPolicyMapsHighCompression(t *testing.T) {
	payload := balancedManagedPolicyPayload()
	payload["compression"] = map[string]any{
		"level":                   "high",
		"compressor":              "zstd-better-compression",
		"minimum_file_size_bytes": float64(4096),
		"maximum_file_size_bytes": float64(0),
		"only_extensions":         []any{},
		"never_extensions":        []any{".zip", ".zst"},
	}

	spec, err := parseManagedBackupPolicy(payload)
	if err != nil {
		t.Fatal(err)
	}
	if spec.CompressionLevel != "high" || spec.Compressor != "zstd-better-compression" {
		t.Fatalf("unexpected high compression spec: %#v", spec)
	}
	args := managedBackupPolicyApplyArgs("/tmp/repo.config", "/data", spec)
	if !slices.Contains(args, "--compression=zstd-better-compression") {
		t.Fatalf("expected high compression args: %#v", args)
	}
}

func TestParseManagedBackupPolicyRequiresCompleteGroups(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{
			name: "file filter boolean",
			mutate: func(payload map[string]any) {
				delete(payload["file_filter"].(map[string]any), "ignore_cache_directories")
			},
		},
		{
			name: "advanced setting",
			mutate: func(payload map[string]any) {
				advanced := payload["backup_policy"].(map[string]any)["advanced_settings"].(map[string]any)
				delete(advanced, "skip_unreadable_files")
			},
		},
		{
			name: "compression list",
			mutate: func(payload map[string]any) {
				delete(payload["compression"].(map[string]any), "never_extensions")
			},
		},
		{
			name: "integer size",
			mutate: func(payload map[string]any) {
				payload["compression"].(map[string]any)["minimum_file_size_bytes"] = 4096.5
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			payload := balancedManagedPolicyPayload()
			test.mutate(payload)
			if _, err := parseManagedBackupPolicy(payload); err == nil {
				t.Fatal("expected incomplete managed policy to fail")
			}
		})
	}
}

func TestApplyManagedBackupPolicyRunsResetBeforeApply(t *testing.T) {
	spec, err := parseManagedBackupPolicy(balancedManagedPolicyPayload())
	if err != nil {
		t.Fatal(err)
	}
	original := runManagedPolicyCommand
	t.Cleanup(func() { runManagedPolicyCommand = original })
	var calls [][]string
	runManagedPolicyCommand = func(
		_ context.Context,
		_ time.Duration,
		_ string,
		args []string,
		_ map[string]string,
		_ string,
	) (process.Result, error) {
		calls = append(calls, slices.Clone(args))
		return process.Result{ExitCode: 0}, nil
	}

	result, err := applyManagedBackupPolicy(context.Background(), "kopia", "/tmp/repo.config", nil, "/data", spec)
	if err != nil {
		t.Fatal(err)
	}
	if len(calls) != 2 {
		t.Fatalf("expected reset and apply calls, got %#v", calls)
	}
	if !slices.Contains(calls[0], "--clear-ignore") || slices.Contains(calls[0], "--add-ignore=*.tmp") {
		t.Fatalf("first call must reset managed lists: %#v", calls[0])
	}
	if slices.Contains(calls[1], "--clear-ignore") || !slices.Contains(calls[1], "--add-ignore=*.tmp") {
		t.Fatalf("second call must apply the complete policy: %#v", calls[1])
	}
	if result["compression_level"] != "balanced" {
		t.Fatalf("unexpected policy result: %#v", result)
	}
	applyResult := result["policy_apply"].(map[string]any)
	summary := applyResult["command_summary"].(map[string]any)
	if summary["never_extension_count"] != 2 || summary["compression_level"] != "balanced" {
		t.Fatalf("unexpected sanitized command summary: %#v", summary)
	}
}

func TestApplyManagedBackupPolicyStopsAtFailedPhase(t *testing.T) {
	spec, err := parseManagedBackupPolicy(balancedManagedPolicyPayload())
	if err != nil {
		t.Fatal(err)
	}
	tests := []struct {
		name          string
		failedCall    int
		expectedCalls int
		expectedPhase string
	}{
		{name: "reset", failedCall: 1, expectedCalls: 1, expectedPhase: "reset"},
		{name: "apply", failedCall: 2, expectedCalls: 2, expectedPhase: "apply"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			original := runManagedPolicyCommand
			t.Cleanup(func() { runManagedPolicyCommand = original })
			calls := 0
			runManagedPolicyCommand = func(
				_ context.Context,
				_ time.Duration,
				_ string,
				_ []string,
				_ map[string]string,
				_ string,
			) (process.Result, error) {
				calls++
				if calls == test.failedCall {
					return process.Result{ExitCode: 1, Stderr: "policy failed"}, errors.New("policy failed")
				}
				return process.Result{ExitCode: 0}, nil
			}

			result, err := applyManagedBackupPolicy(context.Background(), "kopia", "/tmp/repo.config", nil, "/data", spec)
			if err == nil {
				t.Fatal("expected policy application to fail")
			}
			if calls != test.expectedCalls {
				t.Fatalf("expected %d call(s), got %d", test.expectedCalls, calls)
			}
			if result["error_code"] != "POLICY_APPLY_FAILED" || result["policy_phase"] != test.expectedPhase {
				t.Fatalf("unexpected failure result: %#v", result)
			}
		})
	}
}

func TestPreparedManagedBackupDoesNotSnapshotAfterPolicyFailure(t *testing.T) {
	spec, err := parseManagedBackupPolicy(balancedManagedPolicyPayload())
	if err != nil {
		t.Fatal(err)
	}
	originalPolicyRunner := runManagedPolicyCommand
	originalSnapshotRunner := runManagedSnapshotCommand
	originalLocks := managedBackupPathLocks
	t.Cleanup(func() {
		runManagedPolicyCommand = originalPolicyRunner
		runManagedSnapshotCommand = originalSnapshotRunner
		managedBackupPathLocks = originalLocks
	})
	managedBackupPathLocks = newManagedBackupPathLockRegistry()
	runManagedPolicyCommand = func(
		context.Context,
		time.Duration,
		string,
		[]string,
		map[string]string,
		string,
	) (process.Result, error) {
		return process.Result{ExitCode: 1}, errors.New("policy failed")
	}
	snapshotCalls := 0
	runManagedSnapshotCommand = func(
		context.Context,
		string,
		[]string,
		map[string]string,
		string,
		process.OutputLineHandler,
	) (process.Result, error) {
		snapshotCalls++
		return process.Result{}, nil
	}

	status, result, _ := runPreparedManagedBackup(
		context.Background(),
		ReporterSink{},
		"task-policy-failure",
		"kopia",
		"/tmp/policy-failure.config",
		nil,
		"/data",
		spec,
		map[string]any{},
	)

	if status != "failed" || result["error_code"] != "POLICY_APPLY_FAILED" {
		t.Fatalf("unexpected policy failure result: status=%q result=%#v", status, result)
	}
	if snapshotCalls != 0 {
		t.Fatalf("snapshot runner called %d time(s) after policy failure", snapshotCalls)
	}
}

func TestPreparedManagedBackupSerializesSameRepositoryPath(t *testing.T) {
	spec, err := parseManagedBackupPolicy(balancedManagedPolicyPayload())
	if err != nil {
		t.Fatal(err)
	}
	originalPolicyRunner := runManagedPolicyCommand
	originalSnapshotRunner := runManagedSnapshotCommand
	originalLocks := managedBackupPathLocks
	t.Cleanup(func() {
		runManagedPolicyCommand = originalPolicyRunner
		runManagedSnapshotCommand = originalSnapshotRunner
		managedBackupPathLocks = originalLocks
	})
	managedBackupPathLocks = newManagedBackupPathLockRegistry()

	var policyCalls atomic.Int32
	runManagedPolicyCommand = func(
		context.Context,
		time.Duration,
		string,
		[]string,
		map[string]string,
		string,
	) (process.Result, error) {
		policyCalls.Add(1)
		return process.Result{}, nil
	}
	firstSnapshotStarted := make(chan struct{})
	releaseFirstSnapshot := make(chan struct{})
	var snapshotCalls atomic.Int32
	runManagedSnapshotCommand = func(
		context.Context,
		string,
		[]string,
		map[string]string,
		string,
		process.OutputLineHandler,
	) (process.Result, error) {
		if snapshotCalls.Add(1) == 1 {
			close(firstSnapshotStarted)
			<-releaseFirstSnapshot
		}
		return process.Result{Stdout: `{"id":"snapshot-test"}`}, nil
	}

	run := func(taskID string, done chan<- string) {
		status, _, _ := runPreparedManagedBackup(
			context.Background(),
			ReporterSink{},
			taskID,
			"kopia",
			"/tmp/serial.config",
			nil,
			"/data",
			spec,
			map[string]any{},
		)
		done <- status
	}
	done := make(chan string, 2)
	go run("task-one", done)
	<-firstSnapshotStarted
	go run("task-two", done)
	time.Sleep(100 * time.Millisecond)
	if got := policyCalls.Load(); got != 2 {
		t.Fatalf("second task entered policy phase before first snapshot completed: calls=%d", got)
	}
	close(releaseFirstSnapshot)
	for range 2 {
		if status := <-done; status != "success" {
			t.Fatalf("unexpected managed backup status %q", status)
		}
	}
	if got := policyCalls.Load(); got != 4 {
		t.Fatalf("expected both reset/apply phases after release, calls=%d", got)
	}
}
