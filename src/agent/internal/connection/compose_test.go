package connection

import (
	"strings"
	"testing"
)

const managedCompose = `services:
  lensnode:
    image: pinned:0.61.3
    environment:
      LENSNODE_SERVER_URL: https://old.example/sourcelens
      LENSNODE_TOKEN: unchanged-secret
    volumes:
      - /workspace:/workspace:rw
`

func TestLensComposeHostAndContainerRouting(t *testing.T) {
	for _, tc := range []struct {
		host, container string
		mapping         bool
	}{
		{"https://127.0.0.1:11443/sourcelens", "https://host.docker.internal:11443/sourcelens", true},
		{"https://localhost/sourcelens", "https://host.docker.internal/sourcelens", true},
		{"https://LOCALHOST:11443/sourcelens", "https://host.docker.internal:11443/sourcelens", true},
		{"https://[::1]:11443/sourcelens", "https://host.docker.internal:11443/sourcelens", true},
		{"https://10.0.100.74:11443/sourcelens", "https://10.0.100.74:11443/sourcelens", false},
		{"https://external-sl.example:11445", "https://external-sl.example:11445", false},
		{"https://host.docker.internal:11443/sourcelens", "https://host.docker.internal:11443/sourcelens", true},
	} {
		t.Run(tc.host, func(t *testing.T) {
			raw, err := patchLensCompose([]byte(managedCompose), tc.host)
			if err != nil {
				t.Fatal(err)
			}
			result := string(raw)
			if !strings.Contains(result, `LENSNODE_SERVER_URL: "`+tc.container+`"`) ||
				strings.Contains(result, "host.docker.internal:host-gateway") != tc.mapping {
				t.Fatalf("incorrect container routing: %s", result)
			}
			for _, keep := range []string{"image: pinned:0.61.3", "LENSNODE_TOKEN: unchanged-secret", "- /workspace:/workspace:rw"} {
				if !strings.Contains(result, keep) {
					t.Fatal("lost", keep)
				}
			}
			again, err := patchLensCompose(raw, tc.host)
			if err != nil || string(again) != result {
				t.Fatal("Compose update is not idempotent", err)
			}
		})
	}
}

func TestLensComposePreservesExistingMappingsAndOtherServices(t *testing.T) {
	for _, entries := range []string{
		`      - "storage.internal:10.0.100.75"` + "\n",
		`      - "host.docker.internal:host-gateway"` + "\n",
	} {
		fixture := strings.Replace(managedCompose, "    environment:", "    extra_hosts:\n"+entries+"    environment:", 1)
		fixture += "  unrelated:\n    image: untouched\n    extra_hosts:\n      - \"host.docker.internal:10.0.100.76\"\n"
		raw, err := patchLensCompose([]byte(fixture), "https://localhost:11443/sourcelens")
		if err != nil {
			t.Fatal(err)
		}
		result := string(raw)
		if strings.Count(result, `"host.docker.internal:host-gateway"`) != 1 ||
			!strings.Contains(result, entries) ||
			!strings.HasSuffix(result, fixture[strings.Index(fixture, "  unrelated:"):]) {
			t.Fatalf("mapping duplicated or unrelated settings changed: %s", result)
		}
	}
}

func TestLensComposeUnsupportedMappingsFailClosed(t *testing.T) {
	for _, extra := range []string{
		"    extra_hosts: [\"host.docker.internal:host-gateway\"]\n",
		"    extra_hosts:\n      host.docker.internal: host-gateway\n",
		"    extra_hosts:\n      - \"host.docker.internal:10.0.100.99\"\n",
	} {
		raw := strings.Replace(managedCompose, "    environment:", extra+"    environment:", 1)
		if _, err := patchLensCompose([]byte(raw), "https://localhost/sourcelens"); err == nil {
			t.Fatalf("accepted unsupported host mapping: %s", extra)
		}
	}
}
