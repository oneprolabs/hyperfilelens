package engine

import (
	"context"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

type ephemeralKopiaServerClientState struct {
	users    int
	cleaning chan struct{}
}

var ephemeralKopiaServerClients = struct {
	sync.Mutex
	states map[string]*ephemeralKopiaServerClientState
}{states: map[string]*ephemeralKopiaServerClientState{}}

func managedRepositoryConfigPath(path string, root string) bool {
	cleaned := filepath.Clean(strings.TrimSpace(path))
	managedRoot := filepath.Clean(strings.TrimSpace(root))
	if cleaned == "." || managedRoot == "." {
		return false
	}
	rel, err := filepath.Rel(managedRoot, cleaned)
	return err == nil && rel != "." && rel != ".." &&
		!filepath.IsAbs(rel) && !strings.HasPrefix(rel, ".."+string(os.PathSeparator))
}

// acquireEphemeralKopiaServerClientState keeps a temporary connection's local
// files alive while Agent tasks are actively using them. The final release
// starts cleanup in the background so deleting a large cache cannot delay the
// task result.
func (e *Engine) acquireEphemeralKopiaServerClientState(
	ctx context.Context,
	spec repositorySpec,
) func() {
	if spec.Type != "kopia_server" || strings.TrimSpace(spec.SessionID) == "" {
		return func() {}
	}
	configFile := e.repositoryConfigPath(spec)
	configRoot := managedRepositoryCacheRoot(e.current())
	if !managedRepositoryConfigPath(configFile, configRoot) {
		slog.WarnContext(ctx, "ephemeral repository client uses unmanaged config; local cleanup skipped")
		return func() {}
	}
	key := filepath.Clean(configFile)

	for {
		ephemeralKopiaServerClients.Lock()
		state := ephemeralKopiaServerClients.states[key]
		if state == nil {
			state = &ephemeralKopiaServerClientState{users: 1}
			ephemeralKopiaServerClients.states[key] = state
			ephemeralKopiaServerClients.Unlock()
			return e.ephemeralKopiaServerClientRelease(key, state)
		}
		if state.cleaning == nil {
			state.users++
			ephemeralKopiaServerClients.Unlock()
			return e.ephemeralKopiaServerClientRelease(key, state)
		}
		cleaning := state.cleaning
		ephemeralKopiaServerClients.Unlock()
		select {
		case <-ctx.Done():
			return func() {}
		case <-cleaning:
		}
	}
}

func (e *Engine) ephemeralKopiaServerClientRelease(
	configFile string,
	state *ephemeralKopiaServerClientState,
) func() {
	var once sync.Once
	return func() {
		once.Do(func() {
			ephemeralKopiaServerClients.Lock()
			current := ephemeralKopiaServerClients.states[configFile]
			if current != state || state.users <= 0 {
				ephemeralKopiaServerClients.Unlock()
				return
			}
			state.users--
			if state.users > 0 {
				ephemeralKopiaServerClients.Unlock()
				return
			}
			state.cleaning = make(chan struct{})
			ephemeralKopiaServerClients.Unlock()

			go func() {
				e.cleanupEphemeralKopiaServerClient(configFile)

				ephemeralKopiaServerClients.Lock()
				if ephemeralKopiaServerClients.states[configFile] == state {
					delete(ephemeralKopiaServerClients.states, configFile)
				}
				close(state.cleaning)
				ephemeralKopiaServerClients.Unlock()
			}()
		})
	}
}

func (e *Engine) cleanupEphemeralKopiaServerClient(configFile string) {
	if _, err := removeRepositoryLocalState(configFile); err != nil {
		slog.Warn("ephemeral repository client config cleanup failed", "err", err)
	}
	cacheDir := managedRepositoryCacheDir(e.current(), configFile)
	if _, _, err := deleteManagedRepositoryPath(
		context.Background(),
		cacheDir,
		managedRepositoryCacheRoot(e.current()),
	); err != nil {
		slog.Warn("ephemeral repository client cache cleanup failed", "err", err)
	}
}
