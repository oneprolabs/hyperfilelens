package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
)

type managedBackupPolicySpec struct {
	IgnorePatterns         []string
	LargeFileBytesMax      int64
	IgnoreCacheDirectories bool
	CurrentFilesystemOnly  bool
	IgnoreFileErrors       bool
	IgnoreDirectoryErrors  bool
	IgnoreUnknownTypes     bool
	CompressionLevel       string
	Compressor             string
	CompressionMinSize     int64
	CompressionMaxSize     int64
	OnlyExtensions         []string
	NeverExtensions        []string
}

var runManagedPolicyCommand = runProcessWithTimeout

func parseManagedBackupPolicy(raw map[string]any) (managedBackupPolicySpec, error) {
	var spec managedBackupPolicySpec

	filterData, ok := raw["file_filter"].(map[string]any)
	if !ok {
		return spec, fmt.Errorf("file_filter is required")
	}
	configured, err := requiredBoolField(filterData, "configured", "file_filter")
	if err != nil {
		return spec, err
	}
	ignorePatterns, err := requiredStringSliceField(filterData, "ignore_patterns", "file_filter")
	if err != nil {
		return spec, err
	}
	largeFileLimitEnabled, err := requiredBoolField(filterData, "large_file_limit_enabled", "file_filter")
	if err != nil {
		return spec, err
	}
	largeFileBytesMax, err := requiredNonNegativeIntField(filterData, "large_file_bytes_max", "file_filter")
	if err != nil {
		return spec, err
	}
	ignoreCacheDirectories, err := requiredBoolField(filterData, "ignore_cache_directories", "file_filter")
	if err != nil {
		return spec, err
	}
	currentFilesystemOnly, err := requiredBoolField(filterData, "current_filesystem_only", "file_filter")
	if err != nil {
		return spec, err
	}
	if configured {
		spec.IgnorePatterns = ignorePatterns
		spec.IgnoreCacheDirectories = ignoreCacheDirectories
		spec.CurrentFilesystemOnly = currentFilesystemOnly
		if largeFileLimitEnabled {
			if largeFileBytesMax <= 0 {
				return spec, fmt.Errorf("file_filter.large_file_bytes_max must be greater than zero")
			}
			spec.LargeFileBytesMax = largeFileBytesMax
		} else if largeFileBytesMax != 0 {
			return spec, fmt.Errorf("file_filter.large_file_bytes_max must be zero when the limit is disabled")
		}
	} else if len(ignorePatterns) != 0 || largeFileLimitEnabled || largeFileBytesMax != 0 || ignoreCacheDirectories || currentFilesystemOnly {
		return spec, fmt.Errorf("file_filter must contain disabled values when configured is false")
	}

	policyData, ok := raw["backup_policy"].(map[string]any)
	if !ok {
		return spec, fmt.Errorf("backup_policy is required")
	}
	advanced, ok := policyData["advanced_settings"].(map[string]any)
	if !ok {
		return spec, fmt.Errorf("backup_policy.advanced_settings is required")
	}
	advancedEnabled, err := requiredBoolField(advanced, "enabled", "backup_policy.advanced_settings")
	if err != nil {
		return spec, err
	}
	skipUnreadableDirectories, err := requiredBoolField(advanced, "skip_unreadable_directories", "backup_policy.advanced_settings")
	if err != nil {
		return spec, err
	}
	skipUnreadableFiles, err := requiredBoolField(advanced, "skip_unreadable_files", "backup_policy.advanced_settings")
	if err != nil {
		return spec, err
	}
	skipUnsupportedEntries, err := requiredBoolField(advanced, "skip_unsupported_filesystem_entries", "backup_policy.advanced_settings")
	if err != nil {
		return spec, err
	}
	if advancedEnabled {
		spec.IgnoreDirectoryErrors = skipUnreadableDirectories
		spec.IgnoreFileErrors = skipUnreadableFiles
		spec.IgnoreUnknownTypes = skipUnsupportedEntries
	} else if skipUnreadableDirectories || skipUnreadableFiles || skipUnsupportedEntries {
		return spec, fmt.Errorf("backup_policy.advanced_settings skip values must be false when disabled")
	}

	compression, ok := raw["compression"].(map[string]any)
	if !ok {
		return spec, fmt.Errorf("compression is required")
	}
	level, err := requiredStringField(compression, "level", "compression")
	if err != nil {
		return spec, err
	}
	compressor, err := requiredStringField(compression, "compressor", "compression")
	if err != nil {
		return spec, err
	}
	minSize, err := requiredNonNegativeIntField(compression, "minimum_file_size_bytes", "compression")
	if err != nil {
		return spec, err
	}
	maxSize, err := requiredNonNegativeIntField(compression, "maximum_file_size_bytes", "compression")
	if err != nil {
		return spec, err
	}
	onlyExtensions, err := requiredStringSliceField(compression, "only_extensions", "compression")
	if err != nil {
		return spec, err
	}
	neverExtensions, err := requiredStringSliceField(compression, "never_extensions", "compression")
	if err != nil {
		return spec, err
	}
	spec.CompressionLevel = strings.ToLower(level)
	spec.Compressor = compressor
	spec.CompressionMinSize = minSize
	spec.CompressionMaxSize = maxSize
	spec.OnlyExtensions, err = normalizeExtensions(onlyExtensions)
	if err != nil {
		return spec, fmt.Errorf("compression.only_extensions: %w", err)
	}
	spec.NeverExtensions, err = normalizeExtensions(neverExtensions)
	if err != nil {
		return spec, fmt.Errorf("compression.never_extensions: %w", err)
	}
	if len(spec.OnlyExtensions) != 0 {
		return spec, fmt.Errorf("compression.only_extensions must be empty")
	}
	switch spec.CompressionLevel {
	case "none", "balanced", "high":
	default:
		return spec, fmt.Errorf("unsupported compression level %q", spec.CompressionLevel)
	}
	return spec, nil
}

func requiredBoolField(data map[string]any, key string, path string) (bool, error) {
	raw, exists := data[key]
	if !exists {
		return false, fmt.Errorf("%s.%s is required", path, key)
	}
	value, ok := raw.(bool)
	if !ok {
		return false, fmt.Errorf("%s.%s must be a boolean", path, key)
	}
	return value, nil
}

func requiredStringField(data map[string]any, key string, path string) (string, error) {
	raw, exists := data[key]
	if !exists {
		return "", fmt.Errorf("%s.%s is required", path, key)
	}
	value, ok := raw.(string)
	if !ok || strings.TrimSpace(value) == "" {
		return "", fmt.Errorf("%s.%s must be a non-empty string", path, key)
	}
	return strings.TrimSpace(value), nil
}

func requiredNonNegativeIntField(data map[string]any, key string, path string) (int64, error) {
	raw, exists := data[key]
	if !exists {
		return 0, fmt.Errorf("%s.%s is required", path, key)
	}
	switch raw.(type) {
	case int, int32, int64, float64:
	default:
		return 0, fmt.Errorf("%s.%s must be a non-negative integer", path, key)
	}
	if value, ok := raw.(float64); ok && (math.IsNaN(value) || math.IsInf(value, 0) || math.Trunc(value) != value || value > float64(math.MaxInt64)) {
		return 0, fmt.Errorf("%s.%s must be a non-negative integer", path, key)
	}
	value, ok := int64Value(raw)
	if !ok || value < 0 {
		return 0, fmt.Errorf("%s.%s must be a non-negative integer", path, key)
	}
	return value, nil
}

func requiredStringSliceField(data map[string]any, key string, path string) ([]string, error) {
	raw, exists := data[key]
	if !exists {
		return nil, fmt.Errorf("%s.%s is required", path, key)
	}
	var values []any
	switch items := raw.(type) {
	case []any:
		values = items
	case []string:
		values = make([]any, len(items))
		for index, item := range items {
			values[index] = item
		}
	default:
		return nil, fmt.Errorf("%s.%s must be an array of strings", path, key)
	}
	result := make([]string, 0, len(values))
	for _, rawValue := range values {
		value, ok := rawValue.(string)
		if !ok || strings.TrimSpace(value) == "" {
			return nil, fmt.Errorf("%s.%s must contain non-empty strings", path, key)
		}
		result = append(result, strings.TrimSpace(value))
	}
	return result, nil
}

func normalizeExtensions(values []string) ([]string, error) {
	result := make([]string, 0, len(values))
	seen := map[string]struct{}{}
	for _, value := range values {
		item := strings.ToLower(strings.TrimSpace(value))
		if !strings.HasPrefix(item, ".") || strings.ContainsAny(item, " /\\\t\r\n") {
			return nil, fmt.Errorf("invalid extension %q", value)
		}
		if _, ok := seen[item]; ok {
			continue
		}
		seen[item] = struct{}{}
		result = append(result, item)
	}
	return result, nil
}

func managedBackupPolicyResetArgs(configFile string, sourcePath string, disableDotIgnore bool) []string {
	args := []string{
		"--config-file=" + configFile,
		"policy",
		"set",
		"--clear-ignore",
		"--clear-dot-ignore",
		"--clear-only-compress",
		"--clear-never-compress",
	}
	if disableDotIgnore {
		args = append(args, "--disable-dot-ignore")
	} else {
		args = append(args, "--enable-dot-ignore")
	}
	return append(args, sourcePath)
}

func managedBackupPolicyApplyArgs(configFile string, sourcePath string, spec managedBackupPolicySpec, protectedPatterns ...[]string) []string {
	args := []string{
		"--config-file=" + configFile,
		"policy",
		"set",
	}
	for _, pattern := range managedIgnorePatterns(spec.IgnorePatterns, protectedPatterns...) {
		args = append(args, "--add-ignore="+pattern)
	}
	args = append(args,
		"--keep-latest=0",
		"--keep-hourly=0",
		"--keep-daily=0",
		"--keep-weekly=0",
		"--keep-monthly=0",
		"--keep-annual=0",
		"--ignore-identical-snapshots=false",
		"--max-file-size="+strconv.FormatInt(spec.LargeFileBytesMax, 10),
		"--ignore-cache-dirs="+boolFlag(spec.IgnoreCacheDirectories),
		"--one-file-system="+boolFlag(spec.CurrentFilesystemOnly),
		"--ignore-file-errors="+boolFlag(spec.IgnoreFileErrors),
		"--ignore-dir-errors="+boolFlag(spec.IgnoreDirectoryErrors),
		"--ignore-unknown-types="+boolFlag(spec.IgnoreUnknownTypes),
		"--compression="+spec.Compressor,
		"--compression-min-size="+strconv.FormatInt(spec.CompressionMinSize, 10),
		"--compression-max-size="+strconv.FormatInt(spec.CompressionMaxSize, 10),
	)
	for _, extension := range spec.NeverExtensions {
		args = append(args, "--add-never-compress="+extension)
	}
	args = append(args, sourcePath)
	return args
}

func boolFlag(value bool) string {
	if value {
		return "true"
	}
	return "false"
}

func applyManagedBackupPolicy(
	ctx context.Context,
	bin string,
	configFile string,
	env map[string]string,
	sourcePath string,
	spec managedBackupPolicySpec,
	protectedPatterns ...[]string,
) (map[string]any, error) {
	result := map[string]any{"compression_level": spec.CompressionLevel}
	protected := protectedPatternValues(protectedPatterns...)
	resetArgs := managedBackupPolicyResetArgs(configFile, sourcePath, len(protected) > 0)
	resetResult, resetErr := runManagedPolicyCommand(ctx, managedRepositoryKopiaCommandTimeout, bin, resetArgs, env, "")
	resetCommandResult := commandResult(resetResult)
	resetCommandResult["command_summary"] = map[string]any{
		"operation":   "policy_set",
		"phase":       "reset",
		"source_path": sourcePath,
	}
	result["policy_reset"] = resetCommandResult
	if resetErr != nil {
		result["error_code"] = "POLICY_APPLY_FAILED"
		result["policy_phase"] = "reset"
		return result, fmt.Errorf("reset backup policy: %w", resetErr)
	}

	applyArgs := managedBackupPolicyApplyArgs(configFile, sourcePath, spec, protectedPatterns...)
	applyResult, applyErr := runManagedPolicyCommand(ctx, managedRepositoryKopiaCommandTimeout, bin, applyArgs, env, "")
	applyCommandResult := commandResult(applyResult)
	applyCommandResult["command_summary"] = map[string]any{
		"operation":                   "policy_set",
		"phase":                       "apply",
		"source_path":                 sourcePath,
		"compression_level":           spec.CompressionLevel,
		"ignore_pattern_count":        len(managedIgnorePatterns(spec.IgnorePatterns, protectedPatterns...)),
		"system_ignore_pattern_count": len(protected),
		"never_extension_count":       len(spec.NeverExtensions),
	}
	result["policy_apply"] = applyCommandResult
	if applyErr != nil {
		result["error_code"] = "POLICY_APPLY_FAILED"
		result["policy_phase"] = "apply"
		return result, fmt.Errorf("apply backup policy: %w", applyErr)
	}
	return result, nil
}

func protectedPatternValues(values ...[]string) []string {
	if len(values) == 0 {
		return nil
	}
	seen := make(map[string]struct{})
	var out []string
	for _, group := range values {
		for _, value := range group {
			value = strings.TrimSpace(value)
			if value == "" {
				continue
			}
			if _, ok := seen[value]; ok {
				continue
			}
			seen[value] = struct{}{}
			out = append(out, value)
		}
	}
	return out
}

func managedIgnorePatterns(userPatterns []string, protectedPatterns ...[]string) []string {
	protected := protectedPatternValues(protectedPatterns...)
	protectedSet := make(map[string]struct{}, len(protected))
	for _, value := range protected {
		protectedSet[value] = struct{}{}
	}
	seen := make(map[string]struct{}, len(userPatterns))
	out := make([]string, 0, len(userPatterns)+len(protected))
	for _, value := range userPatterns {
		value = strings.TrimSpace(value)
		if value == "" {
			continue
		}
		if _, protectedValue := protectedSet[value]; protectedValue {
			continue
		}
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		out = append(out, value)
	}
	return append(out, protected...)
}

func verifyManagedBackupProtection(
	ctx context.Context,
	bin string,
	configFile string,
	env map[string]string,
	sourcePath string,
	protectedPatterns []string,
) (map[string]any, error) {
	required := protectedPatternValues(protectedPatterns)
	if len(required) == 0 {
		return nil, nil
	}
	result := map[string]any{
		"system_ignore_pattern_count": len(required),
	}
	args := []string{
		"--config-file=" + configFile,
		"policy",
		"show",
		"--json",
		sourcePath,
	}
	res, err := runManagedPolicyCommand(
		ctx,
		managedRepositoryKopiaCommandTimeout,
		bin,
		args,
		env,
		"",
	)
	if err != nil {
		result["error_code"] = "BACKUP_PROTECTION_POLICY_VERIFY_FAILED"
		return result, fmt.Errorf("verify Agent backup boundary policy: %w", err)
	}
	var policy struct {
		Files struct {
			Ignore                 []string `json:"ignore"`
			DotIgnoreFiles         []string `json:"ignoreDotFiles"`
			NoParentDotIgnoreFiles bool     `json:"noParentDotFiles"`
		} `json:"files"`
	}
	if err := json.Unmarshal([]byte(res.Stdout), &policy); err != nil {
		result["error_code"] = "BACKUP_PROTECTION_POLICY_VERIFY_FAILED"
		return result, fmt.Errorf("verify Agent backup boundary policy: invalid Kopia policy response")
	}
	// Kopia de-duplicates and sorts ignore rules when persisting a policy, so
	// the effective JSON order cannot be compared with the insertion order.
	applied := make(map[string]struct{}, len(policy.Files.Ignore))
	for _, pattern := range policy.Files.Ignore {
		applied[strings.TrimSpace(pattern)] = struct{}{}
	}
	for _, pattern := range required {
		if _, ok := applied[pattern]; !ok {
			result["error_code"] = "BACKUP_PROTECTION_POLICY_MISSING"
			return result, fmt.Errorf("required Agent backup boundary policy is missing")
		}
	}
	if !policy.Files.NoParentDotIgnoreFiles || len(policy.Files.DotIgnoreFiles) != 0 {
		result["error_code"] = "BACKUP_PROTECTION_DOT_IGNORE_ENABLED"
		return result, fmt.Errorf("source dot-ignore inheritance is enabled for managed backup")
	}
	result["system_ignore_policy_verified"] = true
	return result, nil
}
