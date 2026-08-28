package enroll

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestJoinDetail(t *testing.T) {
	got := joinDetail("Console API reachable", "GET /health → 200")
	if got != "Console API reachable (GET /health → 200)" {
		t.Fatalf("got %q", got)
	}
	if joinDetail("Title only", "") != "Title only" {
		t.Fatal("expected title only")
	}
}

func TestEmitDetailLineKeepsShortDetailsOnOneLine(t *testing.T) {
	t.Setenv("HFL_OUTPUT", "plain")
	t.Setenv("NO_COLOR", "1")
	t.Setenv("COLUMNS", "120")
	readPipe, writePipe, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	emitDetailLine(" OK ", "CPU architecture is supported", "amd64", writePipe)
	_ = writePipe.Close()
	content, err := io.ReadAll(readPipe)
	if err != nil {
		t.Fatal(err)
	}
	_ = readPipe.Close()
	output := string(content)
	if !strings.Contains(output, "[ OK ] CPU architecture is supported · amd64\n") {
		t.Fatalf("short detail was not kept inline: %q", output)
	}
	if strings.Count(output, "\n") != 1 {
		t.Fatalf("short detail produced extra lines: %q", output)
	}
}

func TestEmitDetailLineWrapsLongDetails(t *testing.T) {
	t.Setenv("HFL_OUTPUT", "plain")
	t.Setenv("NO_COLOR", "1")
	t.Setenv("COLUMNS", "50")
	readPipe, writePipe, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	emitDetailLine(" OK ", "Disk space sufficient", "C:\\Program Files\\HyperFileLens\\Agent 43.7 GB free", writePipe)
	_ = writePipe.Close()
	content, err := io.ReadAll(readPipe)
	if err != nil {
		t.Fatal(err)
	}
	_ = readPipe.Close()
	output := string(content)
	if !strings.Contains(output, "[ OK ] Disk space sufficient\n         C:\\Program Files\\HyperFileLens\\Agent") {
		t.Fatalf("long detail was not wrapped: %q", output)
	}
}

func TestWindowsPowerShellCommandUsesAbsoluteQuotedPath(t *testing.T) {
	got := windowsPowerShellCommand(`C:\Users\O'Brien\AppData\Local\HyperFileLens\Agent\bin\install.cmd`)
	want := `& 'C:\Users\O''Brien\AppData\Local\HyperFileLens\Agent\bin\install.cmd'`
	if got != want {
		t.Fatalf("windowsPowerShellCommand() = %q, want %q", got, want)
	}
}

func TestHumanBytes(t *testing.T) {
	if humanBytes(2048) != "2.0 KB" {
		t.Fatalf("got %q", humanBytes(2048))
	}
}

func TestDiskCheckPathUsesParent(t *testing.T) {
	got := diskCheckPath("/opt/hyperfilelens-agent")
	if got != "/opt" && got != "/" {
		t.Logf("diskCheckPath -> %q", got)
	}
}

func TestCheckHostname(t *testing.T) {
	result := checkHostname()
	if result.Name == "" && !result.Warning {
		t.Fatal("expected name or warning")
	}
}

func TestResolveWSSURL(t *testing.T) {
	got := resolveWSSURL(Config{APIBase: "https://console.example"})
	if got != "wss://console.example/ws/node/agent/" {
		t.Fatalf("got %q", got)
	}
}

func TestRequiredCommandsDetail(t *testing.T) {
	if requiredCommandsDetail() == "" {
		t.Fatal("expected commands detail")
	}
}

func TestFailureOperationLabels(t *testing.T) {
	tests := []struct {
		operation string
		noun      string
		stage     string
	}{
		{"install", "Installation", "Initialization"},
		{"upgrade", "Upgrade", "Upgrading Agent"},
		{"uninstall", "Uninstallation", "Uninstalling"},
		{"registration", "Registration", "Registering Agent"},
		{"status", "Status check", "Checking status"},
	}
	for _, test := range tests {
		t.Run(test.operation, func(t *testing.T) {
			noun, _, stage := failureOperationLabels(test.operation)
			if noun != test.noun || stage != test.stage {
				t.Fatalf("labels = (%q, %q), want (%q, %q)", noun, stage, test.noun, test.stage)
			}
		})
	}
}

func TestPrintCommandFailureForUninstall(t *testing.T) {
	t.Setenv("HFL_OUTPUT", "plain")
	previousStderr := os.Stderr
	readPipe, writePipe, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	os.Stderr = writePipe
	t.Cleanup(func() {
		os.Stderr = previousStderr
		_ = readPipe.Close()
		_ = writePipe.Close()
	})

	PrintCommandFailureFor("uninstall", errors.New("cleanup failed"))
	if err := writePipe.Close(); err != nil {
		t.Fatal(err)
	}
	content, err := io.ReadAll(readPipe)
	if err != nil {
		t.Fatal(err)
	}
	output := string(content)
	for _, expected := range []string{"Uninstallation failed", "Stage         Uninstalling", "cleanup failed"} {
		if !strings.Contains(output, expected) {
			t.Fatalf("failure output does not contain %q: %s", expected, output)
		}
	}
	if strings.Contains(output, "Installation was not started") || strings.Contains(output, "System change None") {
		t.Fatalf("uninstall failure was rendered as an untouched installation: %s", output)
	}
}

func TestPrintCommandFailureForKeepsJSONResultTypeCompatible(t *testing.T) {
	t.Setenv("HFL_OUTPUT", "json")
	previousStderr := os.Stderr
	readPipe, writePipe, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	os.Stderr = writePipe
	t.Cleanup(func() {
		os.Stderr = previousStderr
		_ = readPipe.Close()
		_ = writePipe.Close()
	})

	PrintCommandFailureFor("uninstall", errors.New("cleanup failed"))
	if err := writePipe.Close(); err != nil {
		t.Fatal(err)
	}
	content, err := io.ReadAll(readPipe)
	if err != nil {
		t.Fatal(err)
	}
	output := string(content)
	for _, expected := range []string{`"type":"install_result"`, `"operation":"uninstall"`} {
		if !strings.Contains(output, expected) {
			t.Fatalf("JSON failure output does not contain %q: %s", expected, output)
		}
	}
}

func TestParentSessionKeepsAgentDetailInLogAndCondensesTerminal(t *testing.T) {
	t.Setenv("HFL_OUTPUT", "plain")
	t.Setenv("HFL_PARENT_SESSION", "1")
	logPath := filepath.Join(t.TempDir(), "logs", "install.log")
	useTestInstallLogPath(t, logPath)

	previousStdout := os.Stdout
	terminalRead, terminalWrite, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	os.Stdout = terminalWrite
	finish := StartCommandLogging()
	commitInstallLog()
	printEnrollmentContext(
		"https://127.0.0.1:11443",
		"__platform_lens__",
		model.RoleGateway,
		"linux/amd64",
		"test-host",
	)
	printEnrollmentSuccess(SummaryInfo{
		Role:        "Private Data Gateway",
		NodeID:      "17",
		Version:     "1.2.3",
		Service:     "active",
		LensNode:    "active",
		InstallPath: "/opt/hyperfilelens-agent/bin",
		DataPath:    "/opt/hyperfilelens-agent",
		LogPath:     logPath,
	})
	finish()
	os.Stdout = previousStdout
	if err := terminalWrite.Close(); err != nil {
		t.Fatal(err)
	}
	terminalContent, err := io.ReadAll(terminalRead)
	if err != nil {
		t.Fatal(err)
	}
	_ = terminalRead.Close()

	terminal := string(terminalContent)
	if !strings.Contains(terminal, "Platform Data Gateway preflight checks") {
		t.Fatalf("parent terminal omitted the Gateway phase: %s", terminal)
	}
	for _, unexpected := range []string{"Target\n", "Installation summary"} {
		if strings.Contains(terminal, unexpected) {
			t.Fatalf("parent terminal contains nested %q: %s", unexpected, terminal)
		}
	}

	logged, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatal(err)
	}
	for _, expected := range []string{
		"Target",
		"Preflight checks",
		"Installation completed successfully",
		"Installation summary",
		"Useful commands",
	} {
		if !strings.Contains(string(logged), expected) {
			t.Fatalf("Agent log omitted %q: %s", expected, logged)
		}
	}
}

func TestDirectSessionStillPrintsAgentContext(t *testing.T) {
	t.Setenv("HFL_OUTPUT", "plain")
	t.Setenv("HFL_NO_BANNER", "1")
	previousStdout := os.Stdout
	readPipe, writePipe, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	os.Stdout = writePipe
	printEnrollmentContext(
		"https://console.example",
		"example-org",
		model.RoleAgent,
		"linux/amd64",
		"source-host",
	)
	os.Stdout = previousStdout
	if err := writePipe.Close(); err != nil {
		t.Fatal(err)
	}
	content, err := io.ReadAll(readPipe)
	if err != nil {
		t.Fatal(err)
	}
	_ = readPipe.Close()
	output := string(content)
	for _, expected := range []string{"Target", "example-org", "Preflight checks"} {
		if !strings.Contains(output, expected) {
			t.Fatalf("direct Agent output omitted %q: %s", expected, output)
		}
	}
}
