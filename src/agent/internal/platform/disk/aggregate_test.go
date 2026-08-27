package disk

import (
	"errors"
	"runtime"
	"testing"

	"github.com/shirou/gopsutil/v4/disk"
)

func TestNormalizeMountpoint(t *testing.T) {
	if runtime.GOOS == "windows" {
		tests := []struct {
			in   string
			want string
		}{
			{"c:", "C:\\"},
			{"C:", "C:\\"},
			{`d:\`, "D:\\"},
			{`E:/data`, `E:\data`},
		}
		for _, tc := range tests {
			got := normalizeMountpoint(tc.in)
			if got != tc.want {
				t.Fatalf("normalizeMountpoint(%q) = %q, want %q", tc.in, got, tc.want)
			}
		}
		return
	}
	if got := normalizeMountpoint("/"); got != "/" {
		t.Fatalf("normalizeMountpoint(\"/\") = %q, want \"/\"", got)
	}
	if runtime.GOOS == "darwin" {
		if !isSystemOnlyMount("/System/Volumes/Preboot") {
			t.Fatal("Darwin Preboot volume must not count as host capacity")
		}
		if isSystemOnlyMount("/System/Volumes/Data") {
			t.Fatal("Darwin Data volume must remain eligible for host capacity")
		}
	}
}

func TestHostStorageUsage(t *testing.T) {
	total, used, free, count, err := HostStorageUsage()
	if err != nil {
		t.Fatalf("HostStorageUsage() err = %v", err)
	}
	if count <= 0 {
		t.Fatalf("HostStorageUsage() count = %d, want > 0", count)
	}
	if total == 0 {
		t.Fatal("HostStorageUsage() total = 0")
	}
	if used+free > total {
		t.Fatalf("used(%d)+free(%d) > total(%d)", used, free, total)
	}
}

func TestSummarizeStoragePartitionsSeparatesLocalAndNetworkPools(t *testing.T) {
	parts := []disk.PartitionStat{
		{Device: "/dev/mapper/ubuntu--vg-ubuntu--lv", Mountpoint: "/", Fstype: "ext4"},
		{Device: "/dev/sda2", Mountpoint: "/boot", Fstype: "ext4"},
		{Device: "tmpfs", Mountpoint: "/run", Fstype: "tmpfs"},
		{Device: "//192.168.7.148/C/ProgramData/Desktop", Mountpoint: "/var/lib/hfl/mounts/desktop", Fstype: "cifs"},
		{Device: "//192.168.7.148/C", Mountpoint: "/mnt/share", Fstype: "cifs"},
		{Device: "nas.example:/backup", Mountpoint: "/mnt/nfs", Fstype: "nfs4"},
	}
	usage := map[string]*disk.UsageStat{
		"/":                           {Total: 38, Used: 7, Free: 31},
		"/boot":                       {Total: 2, Used: 1, Free: 1},
		"/run":                        {Total: 4, Used: 1, Free: 3},
		"/mnt/share":                  {Total: 100, Used: 38, Free: 62},
		"/var/lib/hfl/mounts/desktop": {Total: 100, Used: 38, Free: 62},
		"/mnt/nfs":                    {Total: 200, Used: 50, Free: 150},
	}

	inventory := summarizeStoragePartitions(parts, func(path string) (*disk.UsageStat, error) {
		value, ok := usage[path]
		if !ok {
			return nil, errors.New("not found")
		}
		return value, nil
	})

	if len(inventory.LocalPools) != 1 {
		t.Fatalf("local pool count = %d, want 1", len(inventory.LocalPools))
	}
	if inventory.LocalPools[0].TotalBytes != 38 || inventory.LocalPools[0].UsedBytes != 7 {
		t.Fatalf("unexpected local pool metrics: %#v", inventory.LocalPools[0])
	}
	if len(inventory.NetworkPools) != 2 {
		t.Fatalf("network pool count = %d, want 2", len(inventory.NetworkPools))
	}
	smbPool := inventory.NetworkPools[1]
	if len(smbPool.MountPoints) != 2 {
		t.Fatalf("SMB mount count = %d, want 2", len(smbPool.MountPoints))
	}
	if smbPool.Key != "network:smb:192.168.7.148/c" {
		t.Fatalf("unexpected network pool key: %q", smbPool.Key)
	}
	if smbPool.Device != "//192.168.7.148/C" {
		t.Fatalf("unexpected network pool device: %q", smbPool.Device)
	}
	if inventory.NetworkPools[0].Key != "network:nfs4:nas.example:/backup" {
		t.Fatalf("unexpected NFS pool key: %q", inventory.NetworkPools[0].Key)
	}
}

func TestSummarizeStoragePartitionsDeduplicatesLocalBindMountsByDevice(t *testing.T) {
	parts := []disk.PartitionStat{
		{Device: "/dev/sdb1", Mountpoint: "/data", Fstype: "ext4"},
		{Device: "/dev/sdb1", Mountpoint: "/srv/repository", Fstype: "ext4"},
	}
	inventory := summarizeStoragePartitions(parts, func(string) (*disk.UsageStat, error) {
		return &disk.UsageStat{Total: 500, Used: 125, Free: 375}, nil
	})
	if len(inventory.LocalPools) != 1 {
		t.Fatalf("local pool count = %d, want 1", len(inventory.LocalPools))
	}
	if len(inventory.LocalPools[0].MountPoints) != 2 {
		t.Fatalf("local mount count = %d, want 2", len(inventory.LocalPools[0].MountPoints))
	}
}

func TestSummarizeStoragePartitionsDeduplicatesDarwinAPFSVolumes(t *testing.T) {
	if runtime.GOOS != "darwin" {
		t.Skip("APFS device identity is Darwin-specific")
	}
	parts := []disk.PartitionStat{
		{Device: "/dev/disk3s1s1", Mountpoint: "/", Fstype: "apfs"},
		{Device: "/dev/disk3s2", Mountpoint: "/System/Volumes/Preboot", Fstype: "apfs"},
		{Device: "/dev/disk3s5", Mountpoint: "/System/Volumes/Data", Fstype: "apfs"},
		{Device: "/dev/disk3s6", Mountpoint: "/System/Volumes/VM", Fstype: "apfs"},
	}
	inventory := summarizeStoragePartitions(parts, func(string) (*disk.UsageStat, error) {
		return &disk.UsageStat{Total: 245, Used: 212, Free: 33}, nil
	})
	if len(inventory.LocalPools) != 1 {
		t.Fatalf("local pool count = %d, want 1: %#v", len(inventory.LocalPools), inventory.LocalPools)
	}
	pool := inventory.LocalPools[0]
	if pool.Key != "local:device:/dev/disk3" {
		t.Fatalf("APFS pool key = %q, want local:device:/dev/disk3", pool.Key)
	}
	if len(pool.MountPoints) != 2 {
		t.Fatalf("APFS user-visible mount count = %d, want 2", len(pool.MountPoints))
	}
	if pool.TotalBytes != 245 || pool.UsedBytes != 212 || pool.FreeBytes != 33 {
		t.Fatalf("APFS capacity must be counted once: %#v", pool)
	}
}

func TestSummarizeStoragePartitionsKeepsDeduplicatedCapacitySnapshotConsistent(t *testing.T) {
	parts := []disk.PartitionStat{
		{Device: "//nas/share", Mountpoint: "/mnt/first", Fstype: "cifs"},
		{Device: "//nas/share", Mountpoint: "/mnt/second", Fstype: "cifs"},
	}
	usage := map[string]*disk.UsageStat{
		"/mnt/first":  {Total: 100, Used: 20, Free: 80},
		"/mnt/second": {Total: 100, Used: 21, Free: 79},
	}

	inventory := summarizeStoragePartitions(parts, func(path string) (*disk.UsageStat, error) {
		return usage[path], nil
	})

	if len(inventory.NetworkPools) != 1 {
		t.Fatalf("network pool count = %d, want 1", len(inventory.NetworkPools))
	}
	pool := inventory.NetworkPools[0]
	if pool.TotalBytes != 100 || pool.UsedBytes != 21 || pool.FreeBytes != 79 {
		t.Fatalf("capacity snapshot is inconsistent: %#v", pool)
	}
}
