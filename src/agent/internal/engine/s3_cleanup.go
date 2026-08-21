package engine

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
	"github.com/aws/smithy-go"
	smithyhttp "github.com/aws/smithy-go/transport/http"
)

const (
	s3CleanupBatchSize        = 500
	s3CleanupMarkerPath       = ".hyperfilelens/repository-owner-v1.json"
	s3CleanupFailureOwnership = "ownership"
)

type s3CleanupClient interface {
	GetObject(context.Context, *s3.GetObjectInput, ...func(*s3.Options)) (*s3.GetObjectOutput, error)
	ListObjectsV2(context.Context, *s3.ListObjectsV2Input, ...func(*s3.Options)) (*s3.ListObjectsV2Output, error)
	ListMultipartUploads(context.Context, *s3.ListMultipartUploadsInput, ...func(*s3.Options)) (*s3.ListMultipartUploadsOutput, error)
	AbortMultipartUpload(context.Context, *s3.AbortMultipartUploadInput, ...func(*s3.Options)) (*s3.AbortMultipartUploadOutput, error)
	ListObjectVersions(context.Context, *s3.ListObjectVersionsInput, ...func(*s3.Options)) (*s3.ListObjectVersionsOutput, error)
	DeleteObjects(context.Context, *s3.DeleteObjectsInput, ...func(*s3.Options)) (*s3.DeleteObjectsOutput, error)
	DeleteObject(context.Context, *s3.DeleteObjectInput, ...func(*s3.Options)) (*s3.DeleteObjectOutput, error)
	DeleteBucket(context.Context, *s3.DeleteBucketInput, ...func(*s3.Options)) (*s3.DeleteBucketOutput, error)
}

// runS3RepositoryCleanup performs the destructive object-store part on the
// Agent. The Controller still owns the lifecycle task and only dispatches this
// operation after its own ownership proof has succeeded.
func (e *Engine) runS3RepositoryCleanup(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	spec repositorySpec,
	deleteBucket bool,
) (string, map[string]any, string) {
	if err := ctx.Err(); err != nil {
		return "failed", nil, "canceled"
	}
	if strings.TrimSpace(spec.Bucket) == "" {
		return "failed", nil, "S3 bucket is required"
	}
	client, err := newS3CleanupClient(spec)
	if err != nil {
		return "failed", nil, err.Error()
	}
	if err := verifyS3CleanupOwnership(ctx, client, spec); err != nil {
		return "failed", map[string]any{
			"ownership_verified": false,
			"failure_class":      s3CleanupFailureOwnership,
			"cleanup_complete":   false,
		}, err.Error()
	}
	_ = sendProgress(ctx, rep, taskID, map[string]any{
		"phase":              "s3_repository_cleanup",
		"ownership_verified": true,
	})
	heartbeatDone := make(chan struct{})
	defer close(heartbeatDone)
	go func() {
		ticker := time.NewTicker(15 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-heartbeatDone:
				return
			case <-ticker.C:
				_ = sendProgress(ctx, rep, taskID, map[string]any{
					"phase":              "s3_repository_cleanup",
					"ownership_verified": true,
				})
			}
		}
	}()

	result, err := deleteS3Prefix(ctx, client, spec.Bucket, spec.Prefix)
	if err != nil {
		return "failed", map[string]any{
			"ownership_verified": true,
			"failure_class":      "storage",
			"cleanup_complete":   false,
		}, err.Error()
	}
	if deleteBucket {
		bucketResult, bucketErr := deleteS3BucketIfEmpty(ctx, client, spec.Bucket)
		result["bucket_cleanup"] = bucketResult
		if bucketErr != nil {
			// Prefix deletion is complete; retain the bucket and let the
			// Controller record the secondary outcome.
			result["bucket_cleanup_error"] = bucketErr.Error()
		}
	}
	result["ownership_verified"] = true
	result["cleanup_complete"] = true
	result["physical_cleanup"] = "deleted"
	result["scope"] = "s3_prefix"
	return "success", result, ""
}

func newS3CleanupClient(spec repositorySpec) (s3CleanupClient, error) {
	endpoint := strings.TrimSpace(spec.Endpoint)
	if endpoint == "" {
		endpoint = "https://s3.amazonaws.com"
	}
	if !strings.Contains(endpoint, "://") {
		scheme := "https"
		if !spec.UseTLS {
			scheme = "http"
		}
		endpoint = scheme + "://" + endpoint
	}
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.Host == "" {
		return nil, fmt.Errorf("S3 endpoint is invalid")
	}
	style := strings.ToLower(strings.TrimSpace(spec.S3URLStyle))
	usePathStyle := style == "path" || (style == "auto" && s3AutoPathStyle(parsed.Hostname(), spec.S3Platform))
	region := strings.TrimSpace(spec.Region)
	if region == "" {
		region = "us-east-1"
	}
	return s3.New(s3.Options{
		BaseEndpoint:               aws.String(endpoint),
		Region:                     region,
		UsePathStyle:               usePathStyle,
		HTTPClient:                 &http.Client{Timeout: 45 * time.Second},
		RetryMaxAttempts:           2,
		RequestChecksumCalculation: aws.RequestChecksumCalculationWhenRequired,
		ResponseChecksumValidation: aws.ResponseChecksumValidationWhenRequired,
		Credentials: credentials.NewStaticCredentialsProvider(
			spec.AccessKeyID,
			spec.SecretAccessKey,
			"",
		),
	}), nil
}

func s3AutoPathStyle(hostname string, platform string) bool {
	if strings.EqualFold(strings.TrimSpace(platform), "custom") {
		return true
	}
	if strings.EqualFold(strings.TrimSpace(platform), "aws") ||
		strings.EqualFold(strings.TrimSpace(platform), "aliyun") ||
		strings.EqualFold(strings.TrimSpace(platform), "huaweicloud") {
		return false
	}
	hostname = strings.ToLower(strings.TrimSpace(hostname))
	if hostname == "localhost" || strings.HasPrefix(hostname, "minio") {
		return true
	}
	if net.ParseIP(hostname) != nil || strings.Contains(hostname, ":") {
		return true
	}
	return !strings.Contains(hostname, ".")
}

func verifyS3CleanupOwnership(
	ctx context.Context,
	client s3CleanupClient,
	spec repositorySpec,
) error {
	expected := map[string]any{
		"deployment_uuid": strings.TrimSpace(spec.Ownership.DeploymentUUID),
		"repository_uuid": strings.TrimSpace(spec.Ownership.RepositoryUUID),
		"location_digest": strings.TrimSpace(spec.Ownership.LocationDigest),
		"format_version":  spec.Ownership.FormatVersion,
		"signature":       strings.TrimSpace(spec.Ownership.Signature),
	}
	prefix := normalizeS3CleanupPrefix(spec.Prefix)
	for _, ancestor := range s3AncestorPrefixes(prefix) {
		marker, err := getS3CleanupMarker(
			ctx,
			client,
			spec.Bucket,
			s3CleanupMarkerKey(ancestor),
		)
		if err != nil {
			return err
		}
		if marker != nil && !matchingS3CleanupMarker(marker, expected) {
			return fmt.Errorf("selected S3 prefix is nested inside another managed repository")
		}
	}
	ownerMarkerKey := s3CleanupMarkerKey(prefix)
	marker, err := getS3CleanupMarker(ctx, client, spec.Bucket, ownerMarkerKey)
	if err != nil {
		return err
	}
	if marker == nil {
		return fmt.Errorf("repository ownership marker is missing; physical data was retained")
	}
	if !matchingS3CleanupMarker(marker, expected) {
		return fmt.Errorf("repository ownership belongs to another repository")
	}
	descendant, err := hasS3DescendantOwnershipMarker(
		ctx,
		client,
		spec.Bucket,
		s3CleanupListingPrefix(prefix),
		ownerMarkerKey,
	)
	if err != nil {
		return err
	}
	if descendant {
		return fmt.Errorf("selected S3 prefix contains another managed repository")
	}
	return nil
}

// verifyS3RepositoryOwnership performs the inexpensive task-entry proof: the
// marker at the configured repository root must exactly match the identity
// supplied by the Controller. Parent/child scans are reserved for create,
// legacy adoption, and destructive cleanup.
func verifyS3RepositoryOwnership(
	ctx context.Context,
	client s3CleanupClient,
	spec repositorySpec,
) error {
	if spec.Ownership == nil {
		return fmt.Errorf("repository ownership payload is required")
	}
	prefix := normalizeS3CleanupPrefix(spec.Prefix)
	marker, err := getS3CleanupMarker(
		ctx,
		client,
		spec.Bucket,
		s3CleanupMarkerKey(prefix),
	)
	if err != nil {
		return err
	}
	if marker == nil {
		return fmt.Errorf("repository ownership marker is missing")
	}
	expected := map[string]any{
		"deployment_uuid": strings.TrimSpace(spec.Ownership.DeploymentUUID),
		"repository_uuid": strings.TrimSpace(spec.Ownership.RepositoryUUID),
		"location_digest": strings.TrimSpace(spec.Ownership.LocationDigest),
		"format_version":  spec.Ownership.FormatVersion,
		"signature":       strings.TrimSpace(spec.Ownership.Signature),
	}
	if !matchingS3CleanupMarker(marker, expected) {
		return fmt.Errorf("physical repository ownership belongs to another repository")
	}
	return nil
}

func hasS3DescendantOwnershipMarker(
	ctx context.Context,
	client s3CleanupClient,
	bucket string,
	prefix string,
	ownerMarkerKey string,
) (bool, error) {
	var token *string
	for {
		output, err := client.ListObjectsV2(ctx, &s3.ListObjectsV2Input{
			Bucket:            aws.String(bucket),
			Prefix:            aws.String(prefix),
			ContinuationToken: token,
			MaxKeys:           aws.Int32(1000),
		})
		if err != nil {
			return false, fmt.Errorf("unable to inspect repository object prefix: %w", err)
		}
		for _, item := range output.Contents {
			key := aws.ToString(item.Key)
			if key != ownerMarkerKey && strings.HasSuffix(key, "/"+s3CleanupMarkerPath) {
				return true, nil
			}
		}
		if output.IsTruncated == nil || !*output.IsTruncated || output.NextContinuationToken == nil {
			return false, nil
		}
		token = output.NextContinuationToken
	}
}

func getS3CleanupMarker(
	ctx context.Context,
	client s3CleanupClient,
	bucket string,
	key string,
) (map[string]any, error) {
	output, err := client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		if isS3NotFound(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("unable to read repository ownership marker: %w", err)
	}
	defer output.Body.Close()
	var marker map[string]any
	if err := json.NewDecoder(output.Body).Decode(&marker); err != nil {
		return nil, fmt.Errorf("repository ownership marker is invalid")
	}
	return marker, nil
}

func matchingS3CleanupMarker(marker, expected map[string]any) bool {
	for _, key := range []string{"deployment_uuid", "repository_uuid", "location_digest", "format_version", "signature"} {
		if fmt.Sprint(marker[key]) != fmt.Sprint(expected[key]) {
			return false
		}
	}
	return true
}

func normalizeS3CleanupPrefix(prefix string) string {
	return strings.Trim(strings.ReplaceAll(strings.TrimSpace(prefix), "\\", "/"), "/")
}

func s3CleanupListingPrefix(prefix string) string {
	prefix = normalizeS3CleanupPrefix(prefix)
	if prefix == "" {
		return ""
	}
	return prefix + "/"
}

func s3CleanupMarkerKey(prefix string) string {
	return s3CleanupListingPrefix(prefix) + s3CleanupMarkerPath
}

func s3AncestorPrefixes(prefix string) []string {
	prefix = normalizeS3CleanupPrefix(prefix)
	if prefix == "" {
		return nil
	}
	parts := strings.Split(prefix, "/")
	ancestors := make([]string, 0, len(parts))
	ancestors = append(ancestors, "")
	for index := 1; index < len(parts); index++ {
		ancestors = append(ancestors, strings.Join(parts[:index], "/"))
	}
	return ancestors
}

func listS3CleanupObjects(
	ctx context.Context,
	client s3CleanupClient,
	bucket string,
	prefix string,
) ([]string, error) {
	var keys []string
	var token *string
	for {
		output, err := client.ListObjectsV2(ctx, &s3.ListObjectsV2Input{
			Bucket:            aws.String(bucket),
			Prefix:            aws.String(prefix),
			ContinuationToken: token,
			MaxKeys:           aws.Int32(1000),
		})
		if err != nil {
			return nil, fmt.Errorf("unable to inspect repository object prefix: %w", err)
		}
		for _, item := range output.Contents {
			if item.Key != nil {
				keys = append(keys, *item.Key)
			}
		}
		if output.IsTruncated == nil || !*output.IsTruncated || output.NextContinuationToken == nil {
			return keys, nil
		}
		token = output.NextContinuationToken
	}
}

func deleteS3Prefix(
	ctx context.Context,
	client s3CleanupClient,
	bucket string,
	prefix string,
) (map[string]any, error) {
	prefix = s3CleanupListingPrefix(prefix)
	ownerMarkerKey := prefix + s3CleanupMarkerPath
	result := map[string]any{
		"bucket":           bucket,
		"prefix":           prefix,
		"deleted_objects":  0,
		"deleted_versions": 0,
		"deleted_markers":  0,
		"aborted_uploads":  0,
	}
	aborted, err := abortS3MultipartUploads(ctx, client, bucket, prefix)
	if err != nil {
		return result, err
	}
	result["aborted_uploads"] = aborted
	versions, markers, ownerVersions, err := deleteS3ObjectVersions(
		ctx,
		client,
		bucket,
		prefix,
		ownerMarkerKey,
	)
	if err != nil {
		return result, err
	}
	result["deleted_versions"] = versions
	result["deleted_markers"] = markers
	objects, ownerObjectPresent, err := deleteS3CurrentObjectsExceptMarker(
		ctx,
		client,
		bucket,
		prefix,
		ownerMarkerKey,
	)
	if err != nil {
		return result, err
	}
	result["deleted_objects"] = result["deleted_objects"].(int) + objects
	// Keep the exact ownership marker until all other destructive work has
	// succeeded. A retry or Controller fallback can then still prove ownership.
	ownerVersions, ownerObjectPresent, err = verifyS3PrefixContainsOnlyMarker(
		ctx,
		client,
		bucket,
		prefix,
		ownerMarkerKey,
	)
	if err != nil {
		return result, err
	}
	if err := deleteS3ObjectIdentifiers(ctx, client, bucket, ownerVersions); err != nil {
		return result, err
	}
	result["deleted_versions"] = result["deleted_versions"].(int) + len(ownerVersions)
	if ownerObjectPresent && len(ownerVersions) == 0 {
		if err := deleteS3Objects(ctx, client, bucket, []string{ownerMarkerKey}); err != nil {
			return result, err
		}
		result["deleted_objects"] = result["deleted_objects"].(int) + 1
	}
	if err := verifyS3PrefixEmpty(ctx, client, bucket, prefix); err != nil {
		return result, err
	}
	return result, nil
}

func deleteS3CurrentObjectsExceptMarker(
	ctx context.Context,
	client s3CleanupClient,
	bucket string,
	prefix string,
	ownerMarkerKey string,
) (int, bool, error) {
	deleted := 0
	ownerPresent := false
	seen := map[string]struct{}{}
	for {
		output, err := client.ListObjectsV2(ctx, &s3.ListObjectsV2Input{
			Bucket:  aws.String(bucket),
			Prefix:  aws.String(prefix),
			MaxKeys: aws.Int32(1000),
		})
		if err != nil {
			return deleted, ownerPresent, fmt.Errorf("unable to inspect repository object prefix: %w", err)
		}
		keys := make([]string, 0, len(output.Contents))
		for _, item := range output.Contents {
			key := aws.ToString(item.Key)
			if key == "" {
				continue
			}
			if key == ownerMarkerKey {
				ownerPresent = true
				continue
			}
			keys = append(keys, key)
		}
		if len(keys) == 0 {
			return deleted, ownerPresent, nil
		}
		fingerprint := strings.Join(keys, "\x00")
		if _, exists := seen[fingerprint]; exists {
			return deleted, ownerPresent, fmt.Errorf("unable to make progress deleting S3 repository objects")
		}
		seen[fingerprint] = struct{}{}
		if err := deleteS3Objects(ctx, client, bucket, keys); err != nil {
			return deleted, ownerPresent, err
		}
		deleted += len(keys)
	}
}

func abortS3MultipartUploads(ctx context.Context, client s3CleanupClient, bucket, prefix string) (int, error) {
	aborted := 0
	seen := map[string]struct{}{}
	for {
		output, err := client.ListMultipartUploads(ctx, &s3.ListMultipartUploadsInput{
			Bucket:     aws.String(bucket),
			Prefix:     aws.String(prefix),
			MaxUploads: aws.Int32(1000),
		})
		if err != nil {
			return aborted, fmt.Errorf("unable to list S3 multipart uploads: %w", err)
		}
		abortedThisPage := 0
		for _, upload := range output.Uploads {
			if upload.Key == nil || upload.UploadId == nil {
				continue
			}
			identity := *upload.Key + "\x00" + *upload.UploadId
			if _, exists := seen[identity]; exists {
				continue
			}
			if _, err := client.AbortMultipartUpload(ctx, &s3.AbortMultipartUploadInput{
				Bucket:   aws.String(bucket),
				Key:      upload.Key,
				UploadId: upload.UploadId,
			}); err != nil {
				return aborted, fmt.Errorf("unable to abort S3 multipart upload: %w", err)
			}
			seen[identity] = struct{}{}
			aborted++
			abortedThisPage++
		}
		if len(output.Uploads) == 0 {
			return aborted, nil
		}
		if abortedThisPage == 0 {
			return aborted, fmt.Errorf("unable to make progress aborting S3 multipart uploads")
		}
	}
}

func deleteS3ObjectVersions(
	ctx context.Context,
	client s3CleanupClient,
	bucket string,
	prefix string,
	ownerMarkerKey string,
) (int, int, []types.ObjectIdentifier, error) {
	versions, markers := 0, 0
	seen := map[string]struct{}{}
	for {
		entries, versionCount, markerCount, ownerVersions, err := scanS3ObjectVersions(
			ctx,
			client,
			bucket,
			prefix,
			ownerMarkerKey,
		)
		if err != nil {
			return versions, markers, ownerVersions, err
		}
		if len(entries) == 0 {
			return versions, markers, ownerVersions, nil
		}
		fingerprintParts := make([]string, 0, len(entries))
		for _, entry := range entries {
			fingerprintParts = append(
				fingerprintParts,
				aws.ToString(entry.Key)+"\x00"+aws.ToString(entry.VersionId),
			)
		}
		fingerprint := strings.Join(fingerprintParts, "\x01")
		if _, exists := seen[fingerprint]; exists {
			return versions, markers, ownerVersions, fmt.Errorf("unable to make progress deleting S3 object versions")
		}
		seen[fingerprint] = struct{}{}
		if err := deleteS3ObjectIdentifiers(ctx, client, bucket, entries); err != nil {
			return versions, markers, ownerVersions, err
		}
		versions += versionCount
		markers += markerCount
	}
}

func scanS3ObjectVersions(
	ctx context.Context,
	client s3CleanupClient,
	bucket string,
	prefix string,
	ownerMarkerKey string,
) ([]types.ObjectIdentifier, int, int, []types.ObjectIdentifier, error) {
	var keyMarker, versionMarker *string
	var ownerVersions []types.ObjectIdentifier
	for {
		output, err := client.ListObjectVersions(ctx, &s3.ListObjectVersionsInput{
			Bucket:          aws.String(bucket),
			Prefix:          aws.String(prefix),
			KeyMarker:       keyMarker,
			VersionIdMarker: versionMarker,
			MaxKeys:         aws.Int32(1000),
		})
		if err != nil {
			return nil, 0, 0, ownerVersions, fmt.Errorf("unable to list S3 object versions: %w", err)
		}
		entries := make([]types.ObjectIdentifier, 0, len(output.Versions)+len(output.DeleteMarkers))
		versionCount, markerCount := 0, 0
		for _, item := range output.Versions {
			if item.Key == nil || item.VersionId == nil {
				continue
			}
			entry := types.ObjectIdentifier{Key: item.Key, VersionId: item.VersionId}
			if *item.Key == ownerMarkerKey {
				ownerVersions = append(ownerVersions, entry)
			} else {
				entries = append(entries, entry)
				versionCount++
			}
		}
		for _, item := range output.DeleteMarkers {
			if item.Key == nil || item.VersionId == nil {
				continue
			}
			entry := types.ObjectIdentifier{Key: item.Key, VersionId: item.VersionId}
			if *item.Key == ownerMarkerKey {
				ownerVersions = append(ownerVersions, entry)
			} else {
				entries = append(entries, entry)
				markerCount++
			}
		}
		if len(entries) > 0 {
			return entries, versionCount, markerCount, ownerVersions, nil
		}
		if output.IsTruncated == nil || !*output.IsTruncated {
			return nil, 0, 0, ownerVersions, nil
		}
		keyMarker = output.NextKeyMarker
		versionMarker = output.NextVersionIdMarker
		if keyMarker == nil {
			return nil, 0, 0, ownerVersions, fmt.Errorf("S3 object version listing did not provide a continuation key")
		}
	}
}

func verifyS3PrefixContainsOnlyMarker(
	ctx context.Context,
	client s3CleanupClient,
	bucket string,
	prefix string,
	ownerMarkerKey string,
) ([]types.ObjectIdentifier, bool, error) {
	entries, _, _, ownerVersions, err := scanS3ObjectVersions(
		ctx,
		client,
		bucket,
		prefix,
		ownerMarkerKey,
	)
	if err != nil {
		return nil, false, err
	}
	if len(entries) > 0 {
		return nil, false, fmt.Errorf("S3 repository prefix still contains object versions before marker cleanup")
	}

	var token *string
	ownerObjectPresent := false
	for {
		output, listErr := client.ListObjectsV2(ctx, &s3.ListObjectsV2Input{
			Bucket:            aws.String(bucket),
			Prefix:            aws.String(prefix),
			ContinuationToken: token,
			MaxKeys:           aws.Int32(1000),
		})
		if listErr != nil {
			return nil, false, fmt.Errorf("unable to inspect repository object prefix: %w", listErr)
		}
		for _, item := range output.Contents {
			key := aws.ToString(item.Key)
			if key == ownerMarkerKey {
				ownerObjectPresent = true
				continue
			}
			if key != "" {
				return nil, false, fmt.Errorf("S3 repository prefix still contains objects before marker cleanup")
			}
		}
		if output.IsTruncated == nil || !*output.IsTruncated {
			break
		}
		token = output.NextContinuationToken
		if token == nil {
			return nil, false, fmt.Errorf("S3 object listing did not provide a continuation token")
		}
	}
	if !ownerObjectPresent {
		return nil, false, fmt.Errorf("repository ownership marker changed before final cleanup")
	}
	uploads, uploadErr := client.ListMultipartUploads(ctx, &s3.ListMultipartUploadsInput{
		Bucket:     aws.String(bucket),
		Prefix:     aws.String(prefix),
		MaxUploads: aws.Int32(1),
	})
	if uploadErr != nil {
		return nil, false, fmt.Errorf("unable to list S3 multipart uploads: %w", uploadErr)
	}
	if len(uploads.Uploads) > 0 {
		return nil, false, fmt.Errorf("S3 repository prefix still contains multipart uploads before marker cleanup")
	}
	return ownerVersions, ownerObjectPresent, nil
}

func deleteS3Objects(ctx context.Context, client s3CleanupClient, bucket string, keys []string) error {
	entries := make([]types.ObjectIdentifier, 0, len(keys))
	for _, key := range keys {
		entries = append(entries, types.ObjectIdentifier{Key: aws.String(key)})
	}
	return deleteS3ObjectIdentifiers(ctx, client, bucket, entries)
}

func deleteS3ObjectIdentifiers(ctx context.Context, client s3CleanupClient, bucket string, entries []types.ObjectIdentifier) error {
	for start := 0; start < len(entries); start += s3CleanupBatchSize {
		end := start + s3CleanupBatchSize
		if end > len(entries) {
			end = len(entries)
		}
		output, err := client.DeleteObjects(ctx, &s3.DeleteObjectsInput{
			Bucket: aws.String(bucket),
			Delete: &types.Delete{Objects: entries[start:end], Quiet: aws.Bool(true)},
		}, addS3DeleteObjectsContentMD5)
		if err != nil {
			if isS3BatchDeleteCompatibilityError(err) {
				if fallbackErr := deleteS3ObjectIdentifiersIndividually(
					ctx,
					client,
					bucket,
					entries[start:end],
				); fallbackErr != nil {
					return fallbackErr
				}
				continue
			}
			return fmt.Errorf("unable to delete S3 objects: %w", err)
		}
		if len(output.Errors) > 0 {
			return fmt.Errorf("unable to delete S3 objects: %s", aws.ToString(output.Errors[0].Code))
		}
	}
	return nil
}

func addS3DeleteObjectsContentMD5(options *s3.Options) {
	// AWS accepts its newer flexible checksum, while Alibaba OSS requires the
	// original S3 Content-MD5 header for multi-object deletion. Send both so
	// the same request remains portable across S3-compatible providers.
	options.APIOptions = append(
		options.APIOptions,
		smithyhttp.AddContentChecksumMiddleware,
	)
}

func isS3BatchDeleteCompatibilityError(err error) bool {
	var apiErr smithy.APIError
	if !errors.As(err, &apiErr) {
		return false
	}
	switch strings.TrimSpace(apiErr.ErrorCode()) {
	case "MissingArgument", "MissingContentMD5", "NotImplemented", "UnsupportedArgument", "UnsupportedOperation":
		return true
	default:
		return false
	}
}

func deleteS3ObjectIdentifiersIndividually(
	ctx context.Context,
	client s3CleanupClient,
	bucket string,
	entries []types.ObjectIdentifier,
) error {
	for _, entry := range entries {
		key := strings.TrimSpace(aws.ToString(entry.Key))
		if key == "" {
			return fmt.Errorf("unable to delete S3 object: object key is missing")
		}
		input := &s3.DeleteObjectInput{
			Bucket: aws.String(bucket),
			Key:    aws.String(key),
		}
		if versionID := strings.TrimSpace(aws.ToString(entry.VersionId)); versionID != "" {
			input.VersionId = aws.String(versionID)
		}
		if _, err := client.DeleteObject(ctx, input); err != nil {
			return fmt.Errorf("unable to delete S3 object %q: %w", key, err)
		}
	}
	return nil
}

func verifyS3PrefixEmpty(ctx context.Context, client s3CleanupClient, bucket, prefix string) error {
	versions, err := client.ListObjectVersions(ctx, &s3.ListObjectVersionsInput{
		Bucket:  aws.String(bucket),
		Prefix:  aws.String(prefix),
		MaxKeys: aws.Int32(1),
	})
	if err != nil {
		return fmt.Errorf("unable to verify S3 object versions: %w", err)
	}
	if len(versions.Versions) > 0 || len(versions.DeleteMarkers) > 0 {
		return fmt.Errorf("S3 repository prefix still contains object versions after cleanup")
	}
	objects, err := client.ListObjectsV2(ctx, &s3.ListObjectsV2Input{
		Bucket:  aws.String(bucket),
		Prefix:  aws.String(prefix),
		MaxKeys: aws.Int32(1),
	})
	if err != nil {
		return fmt.Errorf("unable to inspect repository object prefix: %w", err)
	}
	if len(objects.Contents) > 0 {
		return fmt.Errorf("S3 repository prefix still contains objects after cleanup")
	}
	uploads, err := abortS3MultipartUploads(ctx, client, bucket, prefix)
	if err != nil {
		return err
	}
	if uploads > 0 {
		return fmt.Errorf("S3 repository prefix still contains multipart uploads after cleanup")
	}
	return nil
}

func deleteS3BucketIfEmpty(ctx context.Context, client s3CleanupClient, bucket string) (map[string]any, error) {
	objects, err := client.ListObjectsV2(ctx, &s3.ListObjectsV2Input{
		Bucket:  aws.String(bucket),
		MaxKeys: aws.Int32(1),
	})
	if err != nil {
		return map[string]any{"status": "failed"}, err
	}
	if len(objects.Contents) > 0 {
		return map[string]any{"status": "skipped_not_empty", "reason": "objects_present"}, nil
	}
	if _, err := client.DeleteBucket(ctx, &s3.DeleteBucketInput{Bucket: aws.String(bucket)}); err != nil {
		return map[string]any{"status": "failed"}, err
	}
	return map[string]any{"status": "deleted", "reason": "bucket_empty"}, nil
}

func isS3NotFound(err error) bool {
	message := strings.ToLower(err.Error())
	return strings.Contains(message, "nosuchkey") || strings.Contains(message, "not found") || strings.Contains(message, "status code: 404")
}
