package engine

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"
)

func TestKopiaCachePolicyDefaultsAndSplitsBudget(t *testing.T) {
	policy, err := kopiaCachePolicyFromPayload(Payload{Extra: map[string]any{}})
	if err != nil {
		t.Fatal(err)
	}
	if !policy.Enabled || policy.ContentMB+policy.MetadataMB != defaultKopiaCacheSizeMB {
		t.Fatalf("unexpected default policy: %#v", policy)
	}
	if policy.ContentMB != 768 || policy.MetadataMB != 256 {
		t.Fatalf("unexpected default split: %#v", policy)
	}
}

func TestKopiaCachePolicyPreservesSmallBudget(t *testing.T) {
	policy, err := kopiaCachePolicyFromPayload(Payload{Extra: map[string]any{
		"kopia_cache_size_mb": 1,
	}})
	if err != nil {
		t.Fatal(err)
	}
	if policy.ContentMB+policy.MetadataMB != 1 {
		t.Fatalf("small cache policy exceeds budget: %#v", policy)
	}
}

func TestKopiaCachePolicyZeroDisablesContentAndMetadataCaches(t *testing.T) {
	policy, err := kopiaCachePolicyFromPayload(Payload{Extra: map[string]any{
		"kopia_cache_size_mb": 0,
	}})
	if err != nil {
		t.Fatal(err)
	}
	if policy.Enabled || len(policy.flags()) != 4 || !slices.Contains(policy.flags(), "--content-cache-size-mb=0") {
		t.Fatalf("unexpected disabled policy: %#v flags=%v", policy, policy.flags())
	}
}

func TestKopiaCacheSetDoesNotTriggerRepositoryMaintenance(t *testing.T) {
	policy := managedKopiaCachePolicy{Enabled: true, ContentMB: 768, MetadataMB: 256}
	args := policy.setArgs("repository.config")
	if !slices.Contains(args, "--no-auto-maintenance") {
		t.Fatalf("cache set may trigger repository maintenance: %v", args)
	}
}

func TestKopiaCachePolicyRejectsInvalidValues(t *testing.T) {
	for _, value := range []any{-1, maxKopiaCacheSizeMB + 1, "not-a-number"} {
		if _, err := kopiaCachePolicyFromPayload(Payload{Extra: map[string]any{
			"kopia_cache_size_mb": value,
		}}); err == nil {
			t.Fatalf("expected invalid cache policy for %#v", value)
		}
	}
}

func TestKopiaConfigCachePolicyComparisonAcceptsKopiaMBEncodings(t *testing.T) {
	policy := managedKopiaCachePolicy{Enabled: true, ContentMB: 768, MetadataMB: 256}
	for _, multiplier := range []int64{1_000_000, 1024 * 1024} {
		configFile := filepath.Join(t.TempDir(), "repository.config")
		config := kopiaConfigCachePolicy{Caching: &kopiaConfigCaching{}}
		config.Caching.ContentSoftBytes = policy.ContentMB * multiplier
		config.Caching.ContentHardBytes = policy.ContentMB * multiplier
		config.Caching.MetadataSoftBytes = policy.MetadataMB * multiplier
		config.Caching.MetadataHardBytes = policy.MetadataMB * multiplier
		raw, err := json.Marshal(config)
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(configFile, raw, 0o600); err != nil {
			t.Fatal(err)
		}

		matches, err := kopiaConfigMatchesCachePolicy(configFile, policy)
		if err != nil || !matches {
			t.Fatalf("multiplier %d did not match: matches=%v err=%v", multiplier, matches, err)
		}
	}
}

func TestKopiaConfigCachePolicyComparisonDistinguishesMissingFromDisabled(t *testing.T) {
	policy := managedKopiaCachePolicy{}
	for _, testCase := range []struct {
		name    string
		config  string
		matches bool
	}{
		{name: "missing caching policy", config: `{}`, matches: false},
		{name: "explicitly disabled caching policy", config: `{"caching":{}}`, matches: true},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			configFile := filepath.Join(t.TempDir(), "repository.config")
			if err := os.WriteFile(configFile, []byte(testCase.config), 0o600); err != nil {
				t.Fatal(err)
			}
			matches, err := kopiaConfigMatchesCachePolicy(configFile, policy)
			if err != nil || matches != testCase.matches {
				t.Fatalf("matches=%v want=%v err=%v", matches, testCase.matches, err)
			}
		})
	}
}

func TestApplyManagedKopiaCachePolicySkipsUnchangedConfig(t *testing.T) {
	configFile := filepath.Join(t.TempDir(), "repository.config")
	config := kopiaConfigCachePolicy{Caching: &kopiaConfigCaching{}}
	config.Caching.ContentSoftBytes = 768 * 1_000_000
	config.Caching.ContentHardBytes = 768 * 1_000_000
	config.Caching.MetadataSoftBytes = 256 * 1_000_000
	config.Caching.MetadataHardBytes = 256 * 1_000_000
	raw, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configFile, raw, 0o600); err != nil {
		t.Fatal(err)
	}

	err = applyManagedKopiaCachePolicy(
		context.Background(),
		filepath.Join(t.TempDir(), "missing-kopia"),
		configFile,
		nil,
		managedKopiaCachePolicy{Enabled: true, ContentMB: 768, MetadataMB: 256},
	)
	if err != nil {
		t.Fatalf("unchanged cache policy invoked Kopia: %v", err)
	}
}
