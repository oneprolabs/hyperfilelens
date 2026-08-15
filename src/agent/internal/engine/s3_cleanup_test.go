package engine

import (
	"context"
	"errors"
	"io"
	"strings"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
)

type fakeS3CleanupClient struct {
	getObject     func(*s3.GetObjectInput) (*s3.GetObjectOutput, error)
	listObjects   func(*s3.ListObjectsV2Input) (*s3.ListObjectsV2Output, error)
	listUploads   func(*s3.ListMultipartUploadsInput) (*s3.ListMultipartUploadsOutput, error)
	abortUpload   func(*s3.AbortMultipartUploadInput) (*s3.AbortMultipartUploadOutput, error)
	listVersions  func(*s3.ListObjectVersionsInput) (*s3.ListObjectVersionsOutput, error)
	deleteObjects func(*s3.DeleteObjectsInput) (*s3.DeleteObjectsOutput, error)
	deleteBucket  func(*s3.DeleteBucketInput) (*s3.DeleteBucketOutput, error)
}

func (f *fakeS3CleanupClient) GetObject(_ context.Context, input *s3.GetObjectInput, _ ...func(*s3.Options)) (*s3.GetObjectOutput, error) {
	return f.getObject(input)
}

func (f *fakeS3CleanupClient) ListObjectsV2(_ context.Context, input *s3.ListObjectsV2Input, _ ...func(*s3.Options)) (*s3.ListObjectsV2Output, error) {
	return f.listObjects(input)
}

func (f *fakeS3CleanupClient) ListMultipartUploads(_ context.Context, input *s3.ListMultipartUploadsInput, _ ...func(*s3.Options)) (*s3.ListMultipartUploadsOutput, error) {
	return f.listUploads(input)
}

func (f *fakeS3CleanupClient) AbortMultipartUpload(_ context.Context, input *s3.AbortMultipartUploadInput, _ ...func(*s3.Options)) (*s3.AbortMultipartUploadOutput, error) {
	return f.abortUpload(input)
}

func (f *fakeS3CleanupClient) ListObjectVersions(_ context.Context, input *s3.ListObjectVersionsInput, _ ...func(*s3.Options)) (*s3.ListObjectVersionsOutput, error) {
	return f.listVersions(input)
}

func (f *fakeS3CleanupClient) DeleteObjects(_ context.Context, input *s3.DeleteObjectsInput, _ ...func(*s3.Options)) (*s3.DeleteObjectsOutput, error) {
	return f.deleteObjects(input)
}

func (f *fakeS3CleanupClient) DeleteBucket(_ context.Context, input *s3.DeleteBucketInput, _ ...func(*s3.Options)) (*s3.DeleteBucketOutput, error) {
	return f.deleteBucket(input)
}

func TestVerifyS3CleanupOwnershipRejectsMissingMarker(t *testing.T) {
	client := &fakeS3CleanupClient{
		getObject: func(_ *s3.GetObjectInput) (*s3.GetObjectOutput, error) {
			return nil, errors.New("status code: 404")
		},
	}
	spec := repositorySpec{
		Type:   "s3",
		Bucket: "bucket",
		Prefix: "hfl",
		Ownership: &repositoryOwnership{
			DeploymentUUID: "deployment",
			RepositoryUUID: "repository",
			LocationDigest: "digest",
			FormatVersion:  1,
			Signature:      "signature",
			MarkerPath:     s3CleanupMarkerPath,
		},
	}

	err := verifyS3CleanupOwnership(context.Background(), client, spec)

	if err == nil || !strings.Contains(err.Error(), "marker is missing") {
		t.Fatalf("expected missing ownership marker error, got %v", err)
	}
}

func TestVerifyS3RepositoryOwnershipChecksOnlyCurrentRoot(t *testing.T) {
	marker := `{"deployment_uuid":"deployment","repository_uuid":"repository","location_digest":"digest","format_version":1,"signature":"signature"}`
	client := &fakeS3CleanupClient{
		getObject: func(input *s3.GetObjectInput) (*s3.GetObjectOutput, error) {
			if aws.ToString(input.Key) != "hfl/"+s3CleanupMarkerPath {
				t.Fatalf("unexpected marker key %q", aws.ToString(input.Key))
			}
			return &s3.GetObjectOutput{Body: io.NopCloser(strings.NewReader(marker))}, nil
		},
	}
	spec := repositorySpec{
		Type:   "s3",
		Bucket: "bucket",
		Prefix: "hfl",
		Ownership: &repositoryOwnership{
			DeploymentUUID: "deployment",
			RepositoryUUID: "repository",
			LocationDigest: "digest",
			FormatVersion:  1,
			Signature:      "signature",
			MarkerPath:     s3CleanupMarkerPath,
		},
	}

	if err := verifyS3RepositoryOwnership(context.Background(), client, spec); err != nil {
		t.Fatalf("verifyS3RepositoryOwnership returned error: %v", err)
	}
}

func TestVerifyS3RepositoryOwnershipSupportsBucketRoot(t *testing.T) {
	marker := `{"deployment_uuid":"deployment","repository_uuid":"repository","location_digest":"digest","format_version":1,"signature":"signature"}`
	client := &fakeS3CleanupClient{
		getObject: func(input *s3.GetObjectInput) (*s3.GetObjectOutput, error) {
			if aws.ToString(input.Key) != s3CleanupMarkerPath {
				t.Fatalf("unexpected bucket-root marker key %q", aws.ToString(input.Key))
			}
			return &s3.GetObjectOutput{Body: io.NopCloser(strings.NewReader(marker))}, nil
		},
	}
	spec := repositorySpec{
		Type:   "s3",
		Bucket: "bucket",
		Prefix: "",
		Ownership: &repositoryOwnership{
			DeploymentUUID: "deployment",
			RepositoryUUID: "repository",
			LocationDigest: "digest",
			FormatVersion:  1,
			Signature:      "signature",
			MarkerPath:     s3CleanupMarkerPath,
		},
	}

	if err := verifyS3RepositoryOwnership(context.Background(), client, spec); err != nil {
		t.Fatalf("bucket-root ownership verification returned error: %v", err)
	}
}

func TestS3BucketRootUsesEmptyListingBoundary(t *testing.T) {
	if got := s3CleanupListingPrefix(""); got != "" {
		t.Fatalf("bucket-root listing prefix = %q, want empty", got)
	}
	if got := s3CleanupMarkerKey(""); got != s3CleanupMarkerPath {
		t.Fatalf("bucket-root marker key = %q, want %q", got, s3CleanupMarkerPath)
	}
	ancestors := s3AncestorPrefixes("hfl/child")
	if len(ancestors) != 2 || ancestors[0] != "" || ancestors[1] != "hfl" {
		t.Fatalf("S3 ancestors = %#v, want bucket root and hfl", ancestors)
	}
}

func TestVerifyS3RepositoryOwnershipRejectsDifferentUUID(t *testing.T) {
	marker := `{"deployment_uuid":"deployment","repository_uuid":"other","location_digest":"digest","format_version":1,"signature":"signature"}`
	client := &fakeS3CleanupClient{
		getObject: func(_ *s3.GetObjectInput) (*s3.GetObjectOutput, error) {
			return &s3.GetObjectOutput{Body: io.NopCloser(strings.NewReader(marker))}, nil
		},
	}
	spec := repositorySpec{
		Type:   "s3",
		Bucket: "bucket",
		Prefix: "hfl",
		Ownership: &repositoryOwnership{
			DeploymentUUID: "deployment",
			RepositoryUUID: "repository",
			LocationDigest: "digest",
			FormatVersion:  1,
			Signature:      "signature",
			MarkerPath:     s3CleanupMarkerPath,
		},
	}

	err := verifyS3RepositoryOwnership(context.Background(), client, spec)
	if err == nil || !strings.Contains(err.Error(), "another repository") {
		t.Fatalf("expected UUID ownership error, got %v", err)
	}
}

func TestDeleteS3PrefixDeletesOwnerMarkerLast(t *testing.T) {
	ownerMarker := "hfl/" + s3CleanupMarkerPath
	versionCalls := 0
	objectCalls := 0
	var deletionBatches [][]string
	client := &fakeS3CleanupClient{
		listUploads: func(_ *s3.ListMultipartUploadsInput) (*s3.ListMultipartUploadsOutput, error) {
			return &s3.ListMultipartUploadsOutput{IsTruncated: aws.Bool(false)}, nil
		},
		listVersions: func(_ *s3.ListObjectVersionsInput) (*s3.ListObjectVersionsOutput, error) {
			versionCalls++
			switch versionCalls {
			case 1:
				return &s3.ListObjectVersionsOutput{
					IsTruncated: aws.Bool(false),
					Versions: []types.ObjectVersion{
						{Key: aws.String("hfl/data"), VersionId: aws.String("data-v1")},
						{Key: aws.String(ownerMarker), VersionId: aws.String("owner-v1")},
					},
				}, nil
			case 2, 3:
				return &s3.ListObjectVersionsOutput{
					IsTruncated: aws.Bool(false),
					Versions: []types.ObjectVersion{
						{Key: aws.String(ownerMarker), VersionId: aws.String("owner-v1")},
					},
				}, nil
			}
			return &s3.ListObjectVersionsOutput{IsTruncated: aws.Bool(false)}, nil
		},
		listObjects: func(_ *s3.ListObjectsV2Input) (*s3.ListObjectsV2Output, error) {
			objectCalls++
			switch objectCalls {
			case 1:
				return &s3.ListObjectsV2Output{
					IsTruncated: aws.Bool(false),
					Contents: []types.Object{
						{Key: aws.String("hfl/data")},
						{Key: aws.String(ownerMarker)},
					},
				}, nil
			case 2, 3:
				return &s3.ListObjectsV2Output{
					IsTruncated: aws.Bool(false),
					Contents: []types.Object{
						{Key: aws.String(ownerMarker)},
					},
				}, nil
			}
			return &s3.ListObjectsV2Output{IsTruncated: aws.Bool(false)}, nil
		},
		deleteObjects: func(input *s3.DeleteObjectsInput) (*s3.DeleteObjectsOutput, error) {
			keys := make([]string, 0, len(input.Delete.Objects))
			for _, item := range input.Delete.Objects {
				keys = append(keys, aws.ToString(item.Key))
			}
			deletionBatches = append(deletionBatches, keys)
			return &s3.DeleteObjectsOutput{}, nil
		},
	}

	result, err := deleteS3Prefix(context.Background(), client, "bucket", "hfl")

	if err != nil {
		t.Fatalf("deleteS3Prefix returned error: %v", err)
	}
	if len(deletionBatches) != 3 {
		t.Fatalf("expected three deletion batches, got %#v", deletionBatches)
	}
	if deletionBatches[len(deletionBatches)-1][0] != ownerMarker {
		t.Fatalf("owner marker was not deleted last: %#v", deletionBatches)
	}
	if got := result["cleanup_complete"]; got != nil {
		t.Fatalf("low-level result should not own lifecycle completion, got %v", got)
	}
}

func TestDeleteS3PrefixRetainsOwnerMarkerWhenResidueReappears(t *testing.T) {
	ownerMarker := "hfl/" + s3CleanupMarkerPath
	versionCalls := 0
	objectCalls := 0
	var deletionBatches [][]string
	client := &fakeS3CleanupClient{
		listUploads: func(_ *s3.ListMultipartUploadsInput) (*s3.ListMultipartUploadsOutput, error) {
			return &s3.ListMultipartUploadsOutput{IsTruncated: aws.Bool(false)}, nil
		},
		listVersions: func(_ *s3.ListObjectVersionsInput) (*s3.ListObjectVersionsOutput, error) {
			versionCalls++
			if versionCalls == 1 {
				return &s3.ListObjectVersionsOutput{
					IsTruncated: aws.Bool(false),
					Versions: []types.ObjectVersion{
						{Key: aws.String(ownerMarker), VersionId: aws.String("owner-v1")},
					},
				}, nil
			}
			return &s3.ListObjectVersionsOutput{
				IsTruncated: aws.Bool(false),
				Versions: []types.ObjectVersion{
					{Key: aws.String(ownerMarker), VersionId: aws.String("owner-v1")},
					{Key: aws.String("hfl/late-data"), VersionId: aws.String("late-v1")},
				},
			}, nil
		},
		listObjects: func(_ *s3.ListObjectsV2Input) (*s3.ListObjectsV2Output, error) {
			objectCalls++
			return &s3.ListObjectsV2Output{
				IsTruncated: aws.Bool(false),
				Contents: []types.Object{
					{Key: aws.String(ownerMarker)},
				},
			}, nil
		},
		deleteObjects: func(input *s3.DeleteObjectsInput) (*s3.DeleteObjectsOutput, error) {
			keys := make([]string, 0, len(input.Delete.Objects))
			for _, item := range input.Delete.Objects {
				keys = append(keys, aws.ToString(item.Key))
			}
			deletionBatches = append(deletionBatches, keys)
			return &s3.DeleteObjectsOutput{}, nil
		},
	}

	_, err := deleteS3Prefix(context.Background(), client, "bucket", "hfl")

	if err == nil || !strings.Contains(err.Error(), "before marker cleanup") {
		t.Fatalf("expected residue verification error, got %v", err)
	}
	for _, batch := range deletionBatches {
		for _, key := range batch {
			if key == ownerMarker {
				t.Fatalf("owner marker was deleted before residue was resolved: %#v", deletionBatches)
			}
		}
	}
}

func TestDeleteS3PrefixRetainsOwnerMarkerWhenObjectDeletionMakesNoProgress(t *testing.T) {
	ownerMarker := "hfl/" + s3CleanupMarkerPath
	var deletionBatches [][]string
	client := &fakeS3CleanupClient{
		listUploads: func(_ *s3.ListMultipartUploadsInput) (*s3.ListMultipartUploadsOutput, error) {
			return &s3.ListMultipartUploadsOutput{IsTruncated: aws.Bool(false)}, nil
		},
		listVersions: func(_ *s3.ListObjectVersionsInput) (*s3.ListObjectVersionsOutput, error) {
			return &s3.ListObjectVersionsOutput{
				IsTruncated: aws.Bool(false),
				Versions: []types.ObjectVersion{
					{Key: aws.String(ownerMarker), VersionId: aws.String("owner-v1")},
				},
			}, nil
		},
		listObjects: func(_ *s3.ListObjectsV2Input) (*s3.ListObjectsV2Output, error) {
			return &s3.ListObjectsV2Output{
				IsTruncated: aws.Bool(false),
				Contents: []types.Object{
					{Key: aws.String("hfl/data")},
					{Key: aws.String(ownerMarker)},
				},
			}, nil
		},
		deleteObjects: func(input *s3.DeleteObjectsInput) (*s3.DeleteObjectsOutput, error) {
			keys := make([]string, 0, len(input.Delete.Objects))
			for _, item := range input.Delete.Objects {
				keys = append(keys, aws.ToString(item.Key))
			}
			deletionBatches = append(deletionBatches, keys)
			return &s3.DeleteObjectsOutput{}, nil
		},
	}

	_, err := deleteS3Prefix(context.Background(), client, "bucket", "hfl")

	if err == nil || !strings.Contains(err.Error(), "make progress") {
		t.Fatalf("expected no-progress error, got %v", err)
	}
	for _, batch := range deletionBatches {
		for _, key := range batch {
			if key == ownerMarker {
				t.Fatalf("owner marker was deleted after no-progress failure: %#v", deletionBatches)
			}
		}
	}
}

func TestDeleteS3PrefixFailsClosedWhenVersionListingIsUnsupported(t *testing.T) {
	deleteCalled := false
	client := &fakeS3CleanupClient{
		listUploads: func(_ *s3.ListMultipartUploadsInput) (*s3.ListMultipartUploadsOutput, error) {
			return &s3.ListMultipartUploadsOutput{IsTruncated: aws.Bool(false)}, nil
		},
		listVersions: func(_ *s3.ListObjectVersionsInput) (*s3.ListObjectVersionsOutput, error) {
			return nil, errors.New("NotImplemented: object version listing is not implemented")
		},
		deleteObjects: func(_ *s3.DeleteObjectsInput) (*s3.DeleteObjectsOutput, error) {
			deleteCalled = true
			return &s3.DeleteObjectsOutput{}, nil
		},
	}

	_, err := deleteS3Prefix(context.Background(), client, "bucket", "hfl")

	if err == nil || !strings.Contains(err.Error(), "object versions") {
		t.Fatalf("expected version-listing error, got %v", err)
	}
	if deleteCalled {
		t.Fatal("cleanup deleted objects without proving version history")
	}
}

func TestGetS3CleanupMarkerRejectsInvalidJSON(t *testing.T) {
	client := &fakeS3CleanupClient{
		getObject: func(_ *s3.GetObjectInput) (*s3.GetObjectOutput, error) {
			return &s3.GetObjectOutput{Body: io.NopCloser(strings.NewReader("not-json"))}, nil
		},
	}

	_, err := getS3CleanupMarker(context.Background(), client, "bucket", "hfl/owner")

	if err == nil || !strings.Contains(err.Error(), "invalid") {
		t.Fatalf("expected invalid marker error, got %v", err)
	}
}
