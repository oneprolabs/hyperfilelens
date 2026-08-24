//go:build !windows

package enroll

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDockerVersionGEHandlesComposePrefix(t *testing.T) {
	tests := []struct {
		name string
		have string
		want string
		ok   bool
	}{
		{name: "lowercase prefix", have: "v5.0.1", want: "2.20.0", ok: true},
		{name: "uppercase prefix", have: "V2.20.0", want: "2.20.0", ok: true},
		{name: "older compose", have: "v2.19.9", want: "2.20.0", ok: false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := dockerVersionGE(tt.have, tt.want); got != tt.ok {
				t.Fatalf("dockerVersionGE(%q, %q) = %v, want %v", tt.have, tt.want, got, tt.ok)
			}
		})
	}
}

func TestDockerComposeCandidateVersionFallsBackToLongOutput(t *testing.T) {
	bin := t.TempDir()
	command := filepath.Join(bin, "docker-compose")
	script := `#!/usr/bin/env sh
if [ "${2:-}" = "--short" ]; then
	exit 1
fi
printf '%s\n' 'Docker Compose version v5.0.1'
`
	if err := os.WriteFile(command, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	if got := dockerComposeCandidateVersion([]string{command}); got != "5.0.1" {
		t.Fatalf("dockerComposeCandidateVersion() = %q, want 5.0.1", got)
	}
}

func TestDockerComposeVersionFallsBackToStandaloneCommand(t *testing.T) {
	bin := t.TempDir()
	writeCommand := func(name, script string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(bin, name), []byte(script), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	writeCommand("docker", `#!/usr/bin/env sh
if [ "${1:-}" = "compose" ]; then
	printf '%s\n' '2.19.9'
	exit 0
fi
exit 1
`)
	writeCommand("docker-compose", `#!/usr/bin/env sh
printf '%s\n' '5.0.1'
`)
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	if got := dockerComposeVersion(); got != "5.0.1" {
		t.Fatalf("dockerComposeVersion() = %q, want standalone 5.0.1", got)
	}
}
