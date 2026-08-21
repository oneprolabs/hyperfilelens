package monitor

import (
	"context"
	"os"
	"runtime"
	"strings"
	"time"

	"github.com/shirou/gopsutil/v4/cpu"
	"github.com/shirou/gopsutil/v4/disk"
	"github.com/shirou/gopsutil/v4/host"
	"github.com/shirou/gopsutil/v4/load"
	"github.com/shirou/gopsutil/v4/mem"
	"github.com/shirou/gopsutil/v4/net"
)

// Sample captures a point-in-time host resource snapshot aligned with control-plane monitor schema.
type Sample struct {
	Timestamp   time.Time      `json:"timestamp"`
	CPU         map[string]any `json:"cpu"`
	Memory      map[string]any `json:"memory"`
	Swap        map[string]any `json:"swap"`
	Disks       []any          `json:"disks"`
	DiskIO      []any          `json:"disk_io"`
	Networks    []any          `json:"networks"`
	LoadAverage []float64      `json:"load_average"`
	BootTime    float64        `json:"boot_time,omitempty"`
	Unavailable []string       `json:"unavailable,omitempty"`
}

// Collector samples CPU, memory, disk, network, and load metrics.
type Collector struct{}

// NewCollector returns a resource monitor collector.
func NewCollector() *Collector {
	return &Collector{}
}

// SampleOnce returns the latest resource snapshot.
func (c *Collector) SampleOnce(ctx context.Context) (Sample, error) {
	_ = c
	if err := ctx.Err(); err != nil {
		return Sample{}, err
	}
	now := time.Now().UTC()
	unavailable := make([]string, 0, 8)

	cpuPayload := map[string]any{}
	if cpuPercent, err := cpu.Percent(100*time.Millisecond, false); err == nil && len(cpuPercent) > 0 {
		cpuPayload["usage_percent"] = cpuPercent[0]
	} else {
		unavailable = appendUnavailable(unavailable, "cpu_usage")
	}
	if perCPU, err := cpu.Percent(0, true); err == nil {
		cpuPayload["per_cpu_percent"] = perCPU
	}
	if logical, err := cpu.Counts(true); err == nil {
		cpuPayload["logical_cores"] = logical
	}
	if physical, err := cpu.Counts(false); err == nil {
		cpuPayload["physical_cores"] = physical
	}
	if freq, err := cpu.Info(); err == nil && len(freq) > 0 {
		cpuPayload["frequency_mhz"] = freq[0].Mhz
	}

	memoryPayload := map[string]any{}
	if vm, err := mem.VirtualMemory(); err == nil && vm != nil {
		memoryPayload = map[string]any{
			"total":     vm.Total,
			"used":      vm.Used,
			"available": vm.Available,
			"percent":   vm.UsedPercent,
		}
	} else {
		unavailable = appendUnavailable(unavailable, "memory")
	}
	swapPayload := map[string]any{}
	if swap, err := mem.SwapMemory(); err == nil && swap != nil {
		swapPayload = map[string]any{
			"total":   swap.Total,
			"used":    swap.Used,
			"free":    swap.Free,
			"percent": swap.UsedPercent,
		}
	} else {
		unavailable = appendUnavailable(unavailable, "swap")
	}

	disks := make([]any, 0)
	if partitions, err := disk.Partitions(false); err == nil {
		for _, part := range partitions {
			mountpoint := normalizeMonitorMountpoint(part.Mountpoint)
			if mountpoint == "" {
				continue
			}
			row := map[string]any{
				"device":     part.Device,
				"mountpoint": mountpoint,
				"fstype":     part.Fstype,
			}
			if usage, usageErr := disk.Usage(mountpoint); usageErr == nil {
				row["total"] = usage.Total
				row["used"] = usage.Used
				row["free"] = usage.Free
				row["percent"] = usage.UsedPercent
			} else {
				unavailable = appendUnavailable(unavailable, "disk_usage")
			}
			disks = append(disks, row)
		}
	} else {
		unavailable = appendUnavailable(unavailable, "disks")
	}

	diskIO := make([]any, 0)
	if counters, err := disk.IOCounters(); err == nil {
		for name, item := range counters {
			diskIO = append(diskIO, map[string]any{
				"name":        name,
				"read_bytes":  item.ReadBytes,
				"write_bytes": item.WriteBytes,
				"read_count":  item.ReadCount,
				"write_count": item.WriteCount,
				"read_time":   item.ReadTime,
				"write_time":  item.WriteTime,
			})
		}
	} else {
		unavailable = appendUnavailable(unavailable, "disk_io")
	}

	networks := make([]any, 0)
	addrByName := map[string][]string{}
	if addrs, err := net.Interfaces(); err == nil {
		for _, iface := range addrs {
			ips := make([]string, 0, len(iface.Addrs))
			for _, addr := range iface.Addrs {
				ips = append(ips, addr.Addr)
			}
			addrByName[iface.Name] = ips
		}
	}
	if counters, err := net.IOCounters(true); err == nil {
		for _, item := range counters {
			networks = append(networks, map[string]any{
				"name":         item.Name,
				"bytes_sent":   item.BytesSent,
				"bytes_recv":   item.BytesRecv,
				"packets_sent": item.PacketsSent,
				"packets_recv": item.PacketsRecv,
				"errin":        item.Errin,
				"errout":       item.Errout,
				"dropin":       item.Dropin,
				"dropout":      item.Dropout,
				"addresses":    addrByName[item.Name],
			})
		}
	} else {
		unavailable = appendUnavailable(unavailable, "networks")
	}

	loadAvg := make([]float64, 0, 3)
	if avg, err := load.Avg(); err == nil && avg != nil {
		loadAvg = []float64{avg.Load1, avg.Load5, avg.Load15}
	} else {
		unavailable = appendUnavailable(unavailable, "load_average")
	}

	var bootTime float64
	if info, err := host.Info(); err == nil {
		bootTime = float64(info.BootTime)
	} else {
		unavailable = appendUnavailable(unavailable, "boot_time")
	}

	return Sample{
		Timestamp:   now,
		CPU:         cpuPayload,
		Memory:      memoryPayload,
		Swap:        swapPayload,
		Disks:       disks,
		DiskIO:      diskIO,
		Networks:    networks,
		LoadAverage: loadAvg,
		BootTime:    bootTime,
		Unavailable: unavailable,
	}, nil
}

// ToPayload converts the sample to the heartbeat metrics payload.
func (s Sample) ToPayload() map[string]any {
	payload := map[string]any{
		"timestamp":    s.Timestamp.Format(time.RFC3339Nano),
		"cpu":          s.CPU,
		"memory":       s.Memory,
		"swap":         s.Swap,
		"disks":        s.Disks,
		"disk_io":      s.DiskIO,
		"networks":     s.Networks,
		"load_average": s.LoadAverage,
	}
	if s.BootTime > 0 {
		payload["boot_time"] = s.BootTime
	}
	if value, ok := nestedFloat(s.CPU, "usage_percent"); ok {
		payload["cpu_usage"] = value
	}
	if value, ok := nestedFloat(s.Memory, "percent"); ok {
		payload["memory_usage"] = value
	}
	if value, ok := nestedFloat(s.Swap, "percent"); ok {
		payload["swap_usage"] = value
	}
	if value, ok := maxDiskPercent(s.Disks); ok {
		payload["disk_usage"] = value
	}
	if value, ok := sumNetworkField(s.Networks, "bytes_recv"); ok {
		payload["network_rx"] = value
	}
	if value, ok := sumNetworkField(s.Networks, "bytes_sent"); ok {
		payload["network_tx"] = value
	}
	if len(s.LoadAverage) > 0 {
		payload["load_1m"] = s.LoadAverage[0]
	}
	if len(s.LoadAverage) > 1 {
		payload["load_5m"] = s.LoadAverage[1]
	}
	if len(s.LoadAverage) > 2 {
		payload["load_15m"] = s.LoadAverage[2]
	}
	collectionStatus := "ready"
	if len(s.Unavailable) > 0 {
		collectionStatus = "partial"
	}
	payload["metadata"] = map[string]any{
		"collector":           "gopsutil",
		"goos":                runtime.GOOS,
		"collection_status":   collectionStatus,
		"unavailable_metrics": s.Unavailable,
	}
	return payload
}

func appendUnavailable(values []string, name string) []string {
	for _, value := range values {
		if value == name {
			return values
		}
	}
	return append(values, name)
}

func nestedFloat(m map[string]any, key string) (float64, bool) {
	if m == nil {
		return 0, false
	}
	switch v := m[key].(type) {
	case float64:
		return v, true
	case float32:
		return float64(v), true
	case int:
		return float64(v), true
	case int64:
		return float64(v), true
	default:
		return 0, false
	}
}

func maxDiskPercent(disks []any) (float64, bool) {
	max := 0.0
	found := false
	for _, row := range disks {
		m, ok := row.(map[string]any)
		if !ok {
			continue
		}
		p, ok := nestedFloat(m, "percent")
		if !ok {
			continue
		}
		found = true
		if p > max {
			max = p
		}
	}
	return max, found
}

func sumNetworkField(networks []any, field string) (float64, bool) {
	total := 0.0
	found := false
	for _, row := range networks {
		m, ok := row.(map[string]any)
		if !ok {
			continue
		}
		switch v := m[field].(type) {
		case float64:
			total += v
			found = true
		case uint64:
			total += float64(v)
			found = true
		case int64:
			total += float64(v)
			found = true
		}
	}
	return total, found
}

func normalizeMonitorMountpoint(mountpoint string) string {
	clean := strings.TrimSpace(mountpoint)
	if runtime.GOOS == "windows" {
		if len(clean) == 2 && clean[1] == ':' {
			return strings.ToUpper(string(clean[0])) + `:\`
		}
		if len(clean) >= 3 && clean[1] == ':' && (clean[2] == '\\' || clean[2] == '/') {
			return strings.ToUpper(string(clean[0])) + `:\`
		}
	}
	return clean
}

// Hostname returns the local hostname for inventory frames.
func Hostname() string {
	h, err := os.Hostname()
	if err != nil {
		return ""
	}
	return h
}
