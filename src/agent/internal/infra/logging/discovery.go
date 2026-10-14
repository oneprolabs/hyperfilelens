package logging

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"time"

	gprocess "github.com/shirou/gopsutil/v4/process"
)

var serverSessionName = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)
var kopiaLogName = regexp.MustCompile(`^kopia-[0-9]{8}-[0-9]{6}-([0-9]+)-[A-Za-z0-9_-]+(?:[.][0-9]+)?[.]log$`)

func plainDirectory(path string) bool {
	info, err := os.Lstat(path)
	return err == nil && info.IsDir() && info.Mode()&os.ModeSymlink == 0
}

// DiscoverServerLogs recovers ownership of retired runtime logs after restart.
// A live PID or unreadable session state is conservatively left untouched.
func DiscoverServerLogs(root string) {
	if !plainDirectory(root) {
		return
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return
	}
	for index, entry := range entries {
		if index >= 4096 {
			break
		}
		if !serverSessionName.MatchString(entry.Name()) {
			continue
		}
		dir := filepath.Join(root, entry.Name())
		if !plainDirectory(dir) {
			continue
		}
		state := filepath.Join(dir, "session.json")
		if info, err := os.Lstat(state); err == nil {
			if !info.Mode().IsRegular() {
				continue
			}
			file, err := os.Open(state)
			if err != nil {
				continue
			}
			var session struct {
				PID int32 `json:"pid"`
			}
			err = json.NewDecoder(io.LimitReader(file, 8192)).Decode(&session)
			_ = file.Close()
			if err != nil || session.PID <= 0 {
				continue
			}
			if exists, err := gprocess.PidExists(session.PID); err != nil || exists {
				continue
			}
		} else if !os.IsNotExist(err) {
			continue
		} else {
			// Missing session state is ambiguous during startup or an
			// interrupted write. Never claim ownership of a possibly active log.
			continue
		}
		path := filepath.Join(dir, "server.log")
		if info, err := os.Lstat(path); err != nil || !info.Mode().IsRegular() {
			continue
		}
		policyLogs.Lock()
		if policyLogs.files[path] == nil && len(policyLogs.files) < 4096 {
			retired := &policyLog{path: path, closed: true}
			retired.closing.Store(true)
			policyLogs.files[path] = retired
		}
		policyLogs.Unlock()
	}
}

// SweepKopiaLogs counts all owned CLI/content segments but only removes
// expired/over-budget segments belonging to dead processes, never symlinks or
// unrelated names. File identity is rechecked at deletion.
func SweepKopiaLogs(root string, days, budget int, now time.Time) error {
	if _, err := os.Lstat(root); os.IsNotExist(err) {
		return nil
	}
	if !plainDirectory(root) {
		return errors.New("Kopia log directory is not a plain directory")
	}
	var candidates []logCandidate
	var total int64
	for _, name := range []string{"cli-logs", "content-logs"} {
		dir := filepath.Join(root, name)
		if _, err := os.Lstat(dir); os.IsNotExist(err) {
			continue
		}
		if !plainDirectory(dir) {
			return errors.New("Kopia log scope is unavailable")
		}
		entries, err := os.ReadDir(dir)
		if err != nil {
			return err
		}
		if len(entries) > 8192 {
			return errors.New("Kopia log scan bound exceeded")
		}
		for _, entry := range entries {
			match := kopiaLogName.FindStringSubmatch(entry.Name())
			if match == nil || entry.Type()&os.ModeSymlink != 0 {
				continue
			}
			info, err := entry.Info()
			if err != nil || !info.Mode().IsRegular() {
				continue
			}
			total += info.Size()
			pid, err := strconv.ParseInt(match[1], 10, 32)
			if err != nil || pid <= 0 {
				continue
			}
			exists, err := gprocess.PidExists(int32(pid))
			if err != nil || exists {
				continue
			}
			candidates = append(candidates, logCandidate{filepath.Join(dir, entry.Name()), info.Size(), info.ModTime(), info})
		}
	}
	sort.Slice(candidates, func(i, j int) bool { return candidates[i].modified.Before(candidates[j].modified) })
	for _, candidate := range candidates {
		if total <= int64(budget)*1024*1024 && now.Sub(candidate.modified) <= time.Duration(days)*24*time.Hour {
			continue
		}
		info, err := os.Lstat(candidate.path)
		if err != nil || !info.Mode().IsRegular() || !os.SameFile(info, candidate.info) {
			continue
		}
		if err := os.Remove(candidate.path); err != nil {
			return err
		}
		total -= candidate.size
	}
	if total > int64(budget)*1024*1024 {
		return errors.New("active Kopia logs exceed their soft budget")
	}
	return nil
}
