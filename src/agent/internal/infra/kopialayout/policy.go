package kopialayout

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"hyperfilelens/agent/internal/platform/disk"
	"hyperfilelens/agent/internal/platform/filelock"
	"hyperfilelens/agent/internal/platform/vfs"
)

const CacheManagementCapability = "cache_management_v1"
const GiBPolicyCapability = "cache_policy_gib_v1"
const GIB int64 = 1 << 30

// Policy is independent of the performance policy. New values use binary GiB; legacy decimal-GB deltas remain readable.
const legacyGB int64 = 1_000_000_000

type Policy struct {
	Version  int   `json:"schema_version"`
	Revision int64 `json:"revision"`
	Blocking int64 `json:"blocking_bytes"`
	Warning  int64 `json:"warning_bytes"`
}

func DefaultPolicy() Policy { return Policy{1, 0, 10 * GIB, 20 * GIB} }
func (p Policy) Validate() error {
	// Compare the delta after checking ordering so malicious values cannot
	// overflow while checking either binary or retained decimal policies.
	if p.Version != 1 || p.Revision < 0 || p.Blocking <= 0 || p.Warning <= p.Blocking {
		return fmt.Errorf("invalid cache policy thresholds")
	}
	delta := p.Warning - p.Blocking
	if delta != 10*GIB && delta != 10*legacyGB {
		return fmt.Errorf("invalid cache policy: warning must equal blocking + 10 GiB (or a retained legacy delta)")
	}
	return nil
}

func managementDir(root string) string {
	return filepath.Join(vfs.AgentDataStoreDir(root), "kopia", "cache-management")
}
func managementPath(root, name string) string {
	return filepath.Join(managementDir(root), name+".json")
}
func saveJSON(root, path string, v any) error {
	data, err := json.Marshal(v)
	if err != nil {
		return err
	}
	return writeDurable(root, path, data)
}
func loadJSON(root, path string, v any) error {
	data, err := readRegular(root, path, 32<<20)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, v)
}
func ReadPolicy(root string) (Policy, error) {
	p := DefaultPolicy()
	err := loadJSON(root, managementPath(root, "policy"), &p)
	if os.IsNotExist(err) {
		return p, nil
	}
	if err != nil {
		return p, err
	}
	return p, p.Validate()
}
func ApplyPolicy(ctx context.Context, root string, p Policy) error {
	if err := p.Validate(); err != nil {
		return err
	}
	if err := privateDir(root, managementDir(root)); err != nil {
		return err
	}
	release, err := filelock.Acquire(ctx, filepath.Join(managementDir(root), "policy.lock"), false)
	if err != nil {
		return err
	}
	defer release()
	old, err := ReadPolicy(root)
	if err != nil {
		return err
	}
	_, statErr := os.Stat(managementPath(root, "policy"))
	if statErr != nil && !os.IsNotExist(statErr) {
		return statErr
	}
	// A missing file is an implicit default, not a persisted revision zero.
	// Accept the control plane's retained revision-zero policy on first use.
	if !os.IsNotExist(statErr) && (p.Revision < old.Revision || p.Revision == old.Revision && p != old) {
		return fmt.Errorf("stale cache policy revision")
	}
	return saveJSON(root, managementPath(root, "policy"), p)
}

type SpaceCheck struct {
	CheckID      string    `json:"check_id"`
	FilesystemID string    `json:"cache_filesystem_id,omitempty"`
	Generation   int64     `json:"cache_generation"`
	State        string    `json:"state"`
	Path         string    `json:"cache_root"`
	Available    uint64    `json:"available_bytes"`
	Policy       Policy    `json:"policy"`
	SampledAt    time.Time `json:"sampled_at"`
	Error        string    `json:"error,omitempty"`
}

// CheckSpace samples the executing account on this filesystem, never inventory.
func CheckSpace(root string, reader func(string) (uint64, uint64, uint64, error)) (SpaceCheck, error) {
	active, activeErr := ReadActiveRoot(root)
	if activeErr != nil {
		policy, _ := ReadPolicy(root)
		return SpaceCheck{CheckID: uuid.NewString(), State: "check_error", Path: active.Path, Generation: active.Generation, Policy: policy, SampledAt: time.Now().UTC(), Error: "Active cache filesystem cannot be verified."}, activeErr
	}
	p, err := ReadPolicy(root)
	check := SpaceCheck{CheckID: uuid.NewString(), Generation: active.Generation, State: "check_error", Path: active.Path, Policy: p, SampledAt: time.Now().UTC()}
	if err != nil {
		return check, err
	}
	path := check.Path
	for {
		_, e := os.Stat(path)
		if e == nil {
			break
		}
		if !os.IsNotExist(e) {
			check.Error = "Cache filesystem cannot be inspected."
			return check, e
		}
		parent := filepath.Dir(path)
		if parent == path {
			return check, e
		}
		path = parent
	}
	if identity, e := directoryIdentity(path); e == nil {
		check.FilesystemID = fmt.Sprintf("%s:%d", strings.Split(identity, ":")[0], active.Generation)
	}
	if reader == nil {
		reader = disk.Usage
	}
	_, _, check.Available, err = reader(path)
	if err != nil {
		check.Error = "Cache filesystem available space cannot be determined."
		return check, fmt.Errorf("CACHE_SPACE_CHECK_FAILED: %w", err)
	}
	switch {
	case check.Available < uint64(p.Blocking):
		check.State = "blocked"
	case check.Available < uint64(p.Warning):
		check.State = "warning"
	default:
		check.State = "normal"
	}
	return check, nil
}

var binarySupport sync.Map

// SupportsDemandDriven probes the actual binary, bounded by time and cached by
// binary identity. A version string alone never activates a new cache protocol.
func SupportsDemandDriven(ctx context.Context, bin string) bool {
	info, err := os.Stat(bin)
	if err != nil {
		return false
	}
	key := fmt.Sprintf("%s:%d:%d", bin, info.Size(), info.ModTime().UnixNano())
	if value, ok := binarySupport.Load(key); ok {
		return value.(bool)
	}
	probe, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	cmd := exec.CommandContext(probe, bin, "--disable-file-logging", "--disable-content-log", "cache", "set", "--help")
	var output limitedOutput
	cmd.Stdout = &output
	cmd.Stderr = &output
	_ = cmd.Run()
	supported := probe.Err() == nil && strings.Contains(output.String(), "cache-demand-driven")
	if probe.Err() == nil {
		binarySupport.Store(key, supported)
	}
	return supported
}

type limitedOutput struct{ data []byte }

func (b *limitedOutput) Write(p []byte) (int, error) {
	n := len(p)
	remaining := 65536 - len(b.data)
	if remaining > 0 {
		if len(p) > remaining {
			p = p[:remaining]
		}
		b.data = append(b.data, p...)
	}
	return n, nil
}
func (b *limitedOutput) String() string { return string(b.data) }

// Unknown is null, never a fabricated zero or an inferred low-space reading.
func (c SpaceCheck) MarshalJSON() ([]byte, error) {
	type alias SpaceCheck
	value := struct {
		alias
		Available *uint64 `json:"available_bytes"`
	}{alias: alias(c)}
	if c.State != "check_error" {
		value.Available = &c.Available
	}
	return json.Marshal(value)
}
