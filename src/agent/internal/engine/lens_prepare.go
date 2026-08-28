package engine

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/google/uuid"
)

const lensWorkspaceIdentityKind = "managed_restore"

const (
	lensWorkspaceTombstoneRetiring = "retiring"
	lensWorkspaceTombstoneRetired  = "retired"
	lensWorkspaceLockTimeout       = 30 * time.Second
)

var removeLensWorkspaceTrash = os.RemoveAll

type lensWorkspaceIdentity struct {
	Version              int    `json:"version"`
	WorkspaceUID         string `json:"workspace_uid"`
	TenantOrganizationID string `json:"tenant_organization_id"`
	GatewayLinkID        string `json:"gateway_link_id"`
	KnowledgeSourceID    string `json:"knowledge_source_id"`
	WorkspaceKind        string `json:"workspace_kind"`
}

type lensWorkspaceTombstone struct {
	Version       int                   `json:"version"`
	State         string                `json:"state"`
	Identity      lensWorkspaceIdentity `json:"identity"`
	WorkspacePath string                `json:"workspace_path"`
	CreatedAt     string                `json:"created_at"`
	UpdatedAt     string                `json:"updated_at"`
}

type lensWorkspacePaths struct {
	Root          string
	Workspace     string
	MetadataRoot  string
	IdentityRoot  string
	Identity      string
	TombstoneRoot string
	Tombstone     string
	LockRoot      string
	Lock          string
	TrashRoot     string
	Trash         string
}

func lensWorkspaceIdentityFromPayload(p Payload) (lensWorkspaceIdentity, error) {
	identity := lensWorkspaceIdentity{
		Version:              1,
		WorkspaceUID:         payloadStringValue(p.Extra["workspace_uid"]),
		TenantOrganizationID: payloadStringValue(p.Extra["tenant_organization_id"]),
		GatewayLinkID:        payloadStringValue(p.Extra["gateway_link_id"]),
		KnowledgeSourceID:    payloadStringValue(p.Extra["knowledge_source_id"]),
		WorkspaceKind:        payloadStringValue(p.Extra["workspace_kind"]),
	}
	if identity.WorkspaceUID == "" || identity.TenantOrganizationID == "" ||
		identity.GatewayLinkID == "" || identity.KnowledgeSourceID == "" {
		return lensWorkspaceIdentity{}, errors.New("managed workspace identity is incomplete")
	}
	if identity.WorkspaceKind != lensWorkspaceIdentityKind {
		return lensWorkspaceIdentity{}, errors.New("unsupported managed workspace kind")
	}
	workspaceUID, err := uuid.Parse(identity.WorkspaceUID)
	if err != nil || workspaceUID.String() != strings.ToLower(identity.WorkspaceUID) {
		return lensWorkspaceIdentity{}, errors.New("managed workspace UID is invalid")
	}
	identity.WorkspaceUID = workspaceUID.String()
	return identity, nil
}

func resolveLensWorkspacePaths(path, workspaceRoot, workspaceUID string) (lensWorkspacePaths, error) {
	rawRoot := strings.TrimSpace(workspaceRoot)
	rawPath := strings.TrimSpace(path)
	for field, value := range map[string]string{"path": rawPath, "workspace_root": rawRoot} {
		if strings.ContainsRune(value, '\x00') || strings.Contains(value, `\`) {
			return lensWorkspacePaths{}, fmt.Errorf("%s contains an unsupported character", field)
		}
		for _, component := range strings.Split(value, "/") {
			if component == "." || component == ".." {
				return lensWorkspacePaths{}, fmt.Errorf("%s contains an unsafe path component", field)
			}
		}
	}
	cleanRoot := filepath.Clean(rawRoot)
	cleanPath := filepath.Clean(rawPath)
	if cleanRoot == "." || cleanPath == "." || !filepath.IsAbs(cleanRoot) || !filepath.IsAbs(cleanPath) {
		return lensWorkspacePaths{}, errors.New("path and workspace_root must be absolute")
	}
	relative, err := filepath.Rel(cleanRoot, cleanPath)
	if err != nil || relative == "." || filepath.IsAbs(relative) || relative == ".." || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
		return lensWorkspacePaths{}, errors.New("path must be a child of workspace_root")
	}
	if strings.Split(relative, string(os.PathSeparator))[0] == lensWorkspaceTrashDirectory {
		return lensWorkspacePaths{}, errors.New("path is reserved for managed workspace cleanup")
	}
	metadataRoot := filepath.Join(filepath.Dir(cleanRoot), ".hyperfilelens")
	identityRoot := filepath.Join(metadataRoot, "identities")
	tombstoneRoot := filepath.Join(metadataRoot, "tombstones")
	lockRoot := filepath.Join(metadataRoot, "locks")
	// Quarantine must stay below the workspace root so rename remains atomic
	// when the workspace root is a dedicated filesystem mount.
	trashRoot := filepath.Join(cleanRoot, lensWorkspaceTrashDirectory)
	return lensWorkspacePaths{
		Root:          cleanRoot,
		Workspace:     cleanPath,
		MetadataRoot:  metadataRoot,
		IdentityRoot:  identityRoot,
		Identity:      filepath.Join(identityRoot, workspaceUID+".json"),
		TombstoneRoot: tombstoneRoot,
		Tombstone:     filepath.Join(tombstoneRoot, workspaceUID+".json"),
		LockRoot:      lockRoot,
		Lock:          filepath.Join(lockRoot, workspaceUID+".lock"),
		TrashRoot:     trashRoot,
		Trash:         filepath.Join(trashRoot, workspaceUID),
	}, nil
}

func ensureLensMetadataLayout(paths lensWorkspacePaths) error {
	base := filepath.Dir(paths.Root)
	for _, path := range []string{
		paths.MetadataRoot,
		paths.IdentityRoot,
		paths.TombstoneRoot,
		paths.LockRoot,
		paths.TrashRoot,
	} {
		if _, _, err := secureEnsureDirectory(path, base, 0o700); err != nil {
			return fmt.Errorf("create protected gateway metadata: %w", err)
		}
		if err := os.Chmod(path, 0o700); err != nil {
			return fmt.Errorf("protect gateway metadata: %w", err)
		}
	}
	return nil
}

func readLensWorkspaceTombstone(path string) (lensWorkspaceTombstone, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return lensWorkspaceTombstone{}, err
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return lensWorkspaceTombstone{}, errors.New("managed workspace tombstone is not a regular file")
	}
	encoded, err := os.ReadFile(path)
	if err != nil {
		return lensWorkspaceTombstone{}, err
	}
	var tombstone lensWorkspaceTombstone
	if err := json.Unmarshal(encoded, &tombstone); err != nil {
		return lensWorkspaceTombstone{}, errors.New("managed workspace tombstone is invalid")
	}
	if tombstone.Version != 1 ||
		(tombstone.State != lensWorkspaceTombstoneRetiring && tombstone.State != lensWorkspaceTombstoneRetired) {
		return lensWorkspaceTombstone{}, errors.New("managed workspace tombstone has an unsupported state")
	}
	return tombstone, nil
}

func validateLensWorkspaceTombstone(
	tombstone lensWorkspaceTombstone,
	identity lensWorkspaceIdentity,
	workspacePath string,
) error {
	if tombstone.Identity != identity || tombstone.WorkspacePath != workspacePath {
		return errors.New("managed workspace tombstone identity does not match")
	}
	return nil
}

func writeLensWorkspaceTombstone(
	path string,
	tombstone lensWorkspaceTombstone,
) error {
	encoded, err := json.Marshal(tombstone)
	if err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".workspace-tombstone-*")
	if err != nil {
		return err
	}
	temporary := file.Name()
	complete := false
	defer func() {
		_ = file.Close()
		if !complete {
			_ = os.Remove(temporary)
		}
	}()
	if err := file.Chmod(0o600); err != nil {
		return err
	}
	if _, err := file.Write(encoded); err != nil {
		return err
	}
	if err := file.Sync(); err != nil {
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	if err := replaceLensWorkspaceTombstone(temporary, path); err != nil {
		return err
	}
	complete = true
	return nil
}

func newLensWorkspaceTombstone(
	identity lensWorkspaceIdentity,
	workspacePath string,
	state string,
	existing *lensWorkspaceTombstone,
) lensWorkspaceTombstone {
	now := time.Now().UTC().Format(time.RFC3339Nano)
	createdAt := now
	if existing != nil && existing.CreatedAt != "" {
		createdAt = existing.CreatedAt
	}
	return lensWorkspaceTombstone{
		Version:       1,
		State:         state,
		Identity:      identity,
		WorkspacePath: workspacePath,
		CreatedAt:     createdAt,
		UpdatedAt:     now,
	}
}

func readLensWorkspaceIdentity(identityPath string) (lensWorkspaceIdentity, error) {
	info, err := os.Lstat(identityPath)
	if err != nil {
		return lensWorkspaceIdentity{}, err
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return lensWorkspaceIdentity{}, errors.New("managed workspace identity is not a regular file")
	}
	identityBytes, err := os.ReadFile(identityPath)
	if err != nil {
		return lensWorkspaceIdentity{}, err
	}
	var identity lensWorkspaceIdentity
	if err := json.Unmarshal(identityBytes, &identity); err != nil {
		return lensWorkspaceIdentity{}, errors.New("managed workspace identity is invalid")
	}
	return identity, nil
}

func writeLensWorkspaceIdentity(identityPath string, identity lensWorkspaceIdentity) error {
	encoded, err := json.Marshal(identity)
	if err != nil {
		return err
	}
	file, err := os.OpenFile(identityPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	complete := false
	defer func() {
		_ = file.Close()
		if !complete {
			_ = os.Remove(identityPath)
		}
	}()
	if _, err := file.Write(encoded); err != nil {
		return err
	}
	if err := file.Sync(); err != nil {
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	complete = true
	return nil
}

func validateLensWorkspaceIdentity(identityPath string, expected lensWorkspaceIdentity) error {
	existing, err := readLensWorkspaceIdentity(identityPath)
	if err != nil {
		return errors.New("managed workspace identity is missing or invalid")
	}
	if existing != expected {
		return errors.New("managed workspace identity does not match")
	}
	return nil
}

func validateLensManagedRestoreTarget(p Payload, targetPath string) error {
	managedPath := payloadStringValue(p.Extra["managed_workspace_path"])
	if managedPath == "" {
		return nil
	}
	identity, err := lensWorkspaceIdentityFromPayload(p)
	if err != nil {
		return err
	}
	paths, err := resolveLensWorkspacePaths(
		managedPath,
		payloadStringValue(p.Extra["workspace_root"]),
		identity.WorkspaceUID,
	)
	if err != nil {
		return err
	}
	fd, cleanWorkspace, err := secureOpenDirectory(paths.Workspace, paths.Root, false, uint64(os.O_RDONLY))
	if err != nil {
		return err
	}
	workspaceDirectory := secureDirectoryFile(fd, cleanWorkspace)
	if workspaceDirectory == nil {
		return errors.New("restricted Data Gateway filesystem operations require Linux")
	}
	_ = workspaceDirectory.Close()
	if err := validateLensWorkspaceIdentity(paths.Identity, identity); err != nil {
		return err
	}

	cleanTarget := filepath.Clean(strings.TrimSpace(targetPath))
	relativeTarget, err := filepath.Rel(paths.Workspace, cleanTarget)
	if err != nil || filepath.IsAbs(relativeTarget) || relativeTarget == ".." || strings.HasPrefix(relativeTarget, ".."+string(os.PathSeparator)) {
		return errors.New("restore target must be inside its managed workspace")
	}
	current := paths.Workspace
	for _, component := range strings.Split(relativeTarget, string(os.PathSeparator)) {
		if component == "." || component == "" {
			continue
		}
		current = filepath.Join(current, component)
		info, statErr := os.Lstat(current)
		if os.IsNotExist(statErr) {
			break
		}
		if statErr != nil {
			return statErr
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("managed restore target contains a symlink: %s", current)
		}
	}
	return nil
}

func (e *Engine) runLensKsPrepare(ctx context.Context, p Payload) (string, map[string]any, string) {
	if err := ctx.Err(); err != nil {
		return "failed", nil, "canceled"
	}
	identity, err := lensWorkspaceIdentityFromPayload(p)
	if err != nil {
		return "failed", nil, err.Error()
	}
	paths, err := resolveLensWorkspacePaths(p.Path, payloadStringValue(p.Extra["workspace_root"]), identity.WorkspaceUID)
	if err != nil {
		return "failed", nil, err.Error()
	}
	if err := ensureLensMetadataLayout(paths); err != nil {
		return "failed", nil, err.Error()
	}
	lockContext, cancel := context.WithTimeout(ctx, lensWorkspaceLockTimeout)
	defer cancel()
	var result map[string]any
	err = withLensWorkspaceLock(lockContext, paths.Lock, func() error {
		tombstone, tombstoneErr := readLensWorkspaceTombstone(paths.Tombstone)
		if tombstoneErr == nil {
			if err := validateLensWorkspaceTombstone(tombstone, identity, paths.Workspace); err != nil {
				return err
			}
			return errors.New("managed workspace UID has been retired")
		}
		if !os.IsNotExist(tombstoneErr) {
			return tombstoneErr
		}

		existing, identityErr := readLensWorkspaceIdentity(paths.Identity)
		if identityErr == nil {
			if existing != identity {
				return errors.New("managed workspace identity does not match")
			}
			cleanPath, created, ensureErr := secureEnsureDirectory(
				paths.Workspace,
				paths.Root,
				0o755,
			)
			if ensureErr != nil {
				return ensureErr
			}
			result = map[string]any{"path": cleanPath, "created": created}
			return nil
		}
		if !os.IsNotExist(identityErr) {
			return identityErr
		}
		if _, statErr := os.Lstat(paths.Workspace); statErr == nil {
			return errors.New("refusing to claim an existing workspace without identity")
		} else if !os.IsNotExist(statErr) {
			return statErr
		}
		// The durable identity is the creation journal. If the process exits before
		// the directory is created, a retry can safely complete the matching claim.
		if err := writeLensWorkspaceIdentity(paths.Identity, identity); err != nil {
			if os.IsExist(err) {
				existing, readErr := readLensWorkspaceIdentity(paths.Identity)
				if readErr != nil || existing != identity {
					return errors.New("managed workspace identity does not match")
				}
			} else {
				return err
			}
		}
		cleanPath, created, ensureErr := secureEnsureDirectory(paths.Workspace, paths.Root, 0o755)
		if ensureErr != nil {
			// Keep the matching identity as a recovery journal for the next retry.
			return ensureErr
		}
		result = map[string]any{"path": cleanPath, "created": created}
		return nil
	})
	if err != nil {
		return "failed", nil, err.Error()
	}
	return "success", result, ""
}

func (e *Engine) runLensKsCleanup(ctx context.Context, p Payload) (string, map[string]any, string) {
	if err := ctx.Err(); err != nil {
		return "failed", nil, "canceled"
	}
	identity, err := lensWorkspaceIdentityFromPayload(p)
	if err != nil {
		return "failed", nil, err.Error()
	}
	paths, err := resolveLensWorkspacePaths(p.Path, payloadStringValue(p.Extra["workspace_root"]), identity.WorkspaceUID)
	if err != nil {
		return "failed", nil, err.Error()
	}
	if err := ensureLensMetadataLayout(paths); err != nil {
		return "failed", nil, err.Error()
	}
	lockContext, cancel := context.WithTimeout(ctx, lensWorkspaceLockTimeout)
	defer cancel()
	removed := false
	alreadyRetired := false
	err = withLensWorkspaceLock(lockContext, paths.Lock, func() error {
		workspaceMissing := pathMissing(paths.Workspace)
		trashMissing := pathMissing(paths.Trash)
		identityMissing := pathMissing(paths.Identity)

		tombstone, tombstoneErr := readLensWorkspaceTombstone(paths.Tombstone)
		if tombstoneErr == nil {
			if err := validateLensWorkspaceTombstone(tombstone, identity, paths.Workspace); err != nil {
				return err
			}
			if tombstone.State == lensWorkspaceTombstoneRetired {
				if !workspaceMissing || !trashMissing || !identityMissing {
					return errors.New("retired managed workspace has unexpected artifacts")
				}
				alreadyRetired = true
				return nil
			}
			if tombstone.State != lensWorkspaceTombstoneRetiring {
				return errors.New("managed workspace tombstone has an unsupported state")
			}
		} else if !os.IsNotExist(tombstoneErr) {
			return tombstoneErr
		}

		if !identityMissing {
			if err := validateLensWorkspaceIdentity(paths.Identity, identity); err != nil {
				return err
			}
		} else if !workspaceMissing || !trashMissing {
			return errors.New("managed workspace identity is missing or invalid")
		}

		retiring := newLensWorkspaceTombstone(
			identity,
			paths.Workspace,
			lensWorkspaceTombstoneRetiring,
			&tombstone,
		)
		if err := writeLensWorkspaceTombstone(paths.Tombstone, retiring); err != nil {
			return err
		}

		workspaceExists := !workspaceMissing
		trashExists := !trashMissing
		if workspaceExists && trashExists {
			return errors.New("managed workspace and trash both exist")
		}
		if workspaceExists {
			fd, _, openErr := secureOpenDirectory(paths.Workspace, paths.Root, false, uint64(os.O_RDONLY))
			if openErr != nil {
				return openErr
			}
			workspaceDirectory := secureDirectoryFile(fd, paths.Workspace)
			if workspaceDirectory == nil {
				return errors.New("restricted Data Gateway filesystem operations require Linux")
			}
			_ = workspaceDirectory.Close()
			if err := os.Rename(paths.Workspace, paths.Trash); err != nil {
				return err
			}
			trashExists = true
		}
		removed = workspaceExists || trashExists
		return nil
	})
	if err != nil {
		return "failed", nil, err.Error()
	}
	if alreadyRetired {
		return "success", map[string]any{"path": paths.Workspace, "removed": false}, ""
	}
	// Removing a potentially large workspace must not hold the lifecycle lock.
	// The retiring tombstone already prevents every late prepare from claiming it.
	if !pathMissing(paths.Trash) {
		if err := removeLensWorkspaceTrash(paths.Trash); err != nil {
			// Identity and the retiring tombstone survive partial deletion so a
			// retry can safely finish removing the quarantined workspace.
			return "failed", nil, err.Error()
		}
	}
	finalLockContext, finalCancel := context.WithTimeout(ctx, lensWorkspaceLockTimeout)
	defer finalCancel()
	err = withLensWorkspaceLock(finalLockContext, paths.Lock, func() error {
		finalTombstone, tombstoneErr := readLensWorkspaceTombstone(paths.Tombstone)
		if tombstoneErr != nil {
			return tombstoneErr
		}
		if err := validateLensWorkspaceTombstone(finalTombstone, identity, paths.Workspace); err != nil {
			return err
		}
		if finalTombstone.State == lensWorkspaceTombstoneRetired {
			if !pathMissing(paths.Workspace) || !pathMissing(paths.Trash) || !pathMissing(paths.Identity) {
				return errors.New("retired managed workspace has unexpected artifacts")
			}
			return nil
		}
		if finalTombstone.State != lensWorkspaceTombstoneRetiring {
			return errors.New("managed workspace tombstone has an unsupported state")
		}
		if !pathMissing(paths.Workspace) || !pathMissing(paths.Trash) {
			return errors.New("managed workspace cleanup is incomplete")
		}
		if err := os.Remove(paths.Identity); err != nil && !os.IsNotExist(err) {
			return err
		}
		retiredTombstone := newLensWorkspaceTombstone(
			identity,
			paths.Workspace,
			lensWorkspaceTombstoneRetired,
			&finalTombstone,
		)
		return writeLensWorkspaceTombstone(paths.Tombstone, retiredTombstone)
	})
	if err != nil {
		return "failed", nil, err.Error()
	}
	return "success", map[string]any{"path": paths.Workspace, "removed": removed}, ""
}

func pathMissing(path string) bool {
	_, err := os.Lstat(path)
	return os.IsNotExist(err)
}
