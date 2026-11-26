package kopialayout

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestAppliedPolicyDistinguishesImplicitDefault(t *testing.T) {
	root := t.TempDir()
	policy, applied, err := ReadAppliedPolicy(root)
	if err != nil || applied || policy != DefaultPolicy() {
		t.Fatalf("implicit policy = %+v, applied=%v err=%v", policy, applied, err)
	}
	env, err := ProcessEnv(root, filepath.Join(root, "logs"), map[string]string{"KOPIA_CACHE_DEMAND_DRIVEN": "1"})
	if err != nil || env["KOPIA_CACHE_DEMAND_DRIVEN"] != "" {
		t.Fatalf("implicit policy enabled demand-driven mode: %v, %v", env, err)
	}
	if err := ApplyPolicy(context.Background(), root, DefaultPolicy()); err != nil {
		t.Fatal(err)
	}
	policy, applied, err = ReadAppliedPolicy(root)
	if err != nil || !applied || policy != DefaultPolicy() {
		t.Fatalf("persisted default = %+v, applied=%v err=%v", policy, applied, err)
	}
	env, err = ProcessEnv(root, filepath.Join(root, "logs"), nil)
	if err != nil || env["KOPIA_CACHE_DEMAND_DRIVEN"] != "1" {
		t.Fatalf("persisted default did not enable managed mode: %v", err)
	}
}

func TestInvalidAppliedPolicyCannotBecomeImplicitDefault(t *testing.T) {
	for _, data := range []string{"{}", "{", `{"schema_version":1,"revision":1}`} {
		t.Run(data, func(t *testing.T) {
			root := t.TempDir()
			path := managementPath(root, "policy")
			if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, []byte(data), 0o600); err != nil {
				t.Fatal(err)
			}
			if _, applied, err := ReadAppliedPolicy(root); !applied || err == nil {
				t.Fatalf("invalid persisted policy treated as default: applied=%v err=%v", applied, err)
			}
			if _, err := ProcessEnv(root, filepath.Join(root, "logs"), nil); err == nil {
				t.Fatal("invalid policy silently downgraded the process environment")
			}
		})
	}
}

func TestUnreadableAppliedPolicyCannotBecomeLegacyMode(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(managementPath(root, "policy"), 0o700); err != nil {
		t.Fatal(err)
	}
	if _, applied, err := ReadAppliedPolicy(root); !applied || err == nil {
		t.Fatalf("non-file policy treated as absent: applied=%v err=%v", applied, err)
	}
	if _, err := ProcessEnv(root, filepath.Join(root, "logs"), nil); err == nil {
		t.Fatal("unreadable policy silently downgraded the process environment")
	}
}
