package kopialayout

import (
	"bytes"
	"context"
	"encoding/binary"
	"fmt"
	"strings"

	gprocess "github.com/shirou/gopsutil/v4/process"
	"golang.org/x/sys/unix"
)

// gopsutil does not implement Environ on Darwin. Read the same kernel procargs
// record used for Cmdline, but return only path keys needed for ownership.
// Tokens and other unrelated environment values must never leave this helper.
func processEnvironment(ctx context.Context, p *gprocess.Process) ([]string, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	data, err := unix.SysctlRaw("kern.procargs2", int(p.Pid))
	if err != nil {
		return nil, err
	}
	if len(data) < 4 {
		return nil, fmt.Errorf("process argument record is incomplete")
	}
	argc := int(binary.NativeEndian.Uint32(data[:4]))
	if argc < 1 || argc > 65536 {
		return nil, fmt.Errorf("process argument count is invalid")
	}
	rest := data[4:]
	end := bytes.IndexByte(rest, 0) // Executable path, then kernel padding.
	if end < 0 {
		return nil, fmt.Errorf("process executable record is incomplete")
	}
	rest = bytes.TrimLeft(rest[end+1:], "\x00")
	for i := 0; i < argc; i++ {
		end = bytes.IndexByte(rest, 0)
		if end < 0 {
			return nil, fmt.Errorf("process argument record is incomplete")
		}
		rest = rest[end+1:]
	}
	var paths []string
	for _, raw := range bytes.Split(rest, []byte{0}) {
		key, _, found := strings.Cut(string(raw), "=")
		if !found {
			continue
		}
		switch key {
		case "KOPIA_CONFIG_PATH", "KOPIA_CACHE_DIRECTORY", "HFL_DATA_DIR", "HFL_AGENT_ROOT", "HFL_AGENT_HOME", "HOME", "USERPROFILE":
			paths = append(paths, string(raw))
		}
	}
	return paths, ctx.Err()
}
