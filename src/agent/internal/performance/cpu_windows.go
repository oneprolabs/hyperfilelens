//go:build windows

package performance

import (
	"context"
	"os"
	"runtime"
	"sync"
	"unsafe"

	gprocess "github.com/shirou/gopsutil/v4/process"
	"golang.org/x/sys/windows"
	"hyperfilelens/agent/internal/model"
)

var cpuJob struct {
	sync.Mutex
	handle windows.Handle
}
var isProcessInJob = windows.NewLazySystemDLL("kernel32.dll").NewProc("IsProcessInJob")

type cpuRateInfo struct {
	Flags uint32
	Rate  uint32
}

func applyCPU(ctx context.Context, _ *model.AgentConfig, cores int) Item {
	cpuJob.Lock()
	defer cpuJob.Unlock()
	if cpuJob.handle == 0 {
		if process, err := gprocess.NewProcess(int32(os.Getpid())); err == nil {
			if children, err := process.ChildrenWithContext(ctx); err == nil && len(children) > 0 {
				return Item{"pending", "Initial shared CPU Job is waiting for existing work processes to finish."}
			}
		}
		job, err := windows.CreateJobObject(nil, nil)
		if err != nil {
			return Item{"unsupported", "CPU Job Object creation failed; work remains unrestricted."}
		}
		handle, err := windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE, false, uint32(os.Getpid()))
		if err != nil {
			_ = windows.CloseHandle(job)
			return Item{"degraded", "Agent process permissions do not allow CPU governance."}
		}
		err = windows.AssignProcessToJobObject(job, handle)
		_ = windows.CloseHandle(handle)
		if err != nil {
			_ = windows.CloseHandle(job)
			return Item{"unsupported", "Existing Job constraints prevent shared CPU governance."}
		}
		cpuJob.handle = job // Keep alive; deliberately no KILL_ON_JOB_CLOSE flag.
	}
	rate := uint32(min(10000, max(1, cores*10000/max(1, runtime.NumCPU()))))
	info := cpuRateInfo{Flags: 1 | 4, Rate: rate} // ENABLE | HARD_CAP (CPU time only).
	if _, err := windows.SetInformationJobObject(cpuJob.handle, windows.JobObjectCpuRateControlInformation,
		uintptr(unsafe.Pointer(&info)), uint32(unsafe.Sizeof(info))); err != nil {
		return Item{"degraded", "CPU rate control could not be applied; previous Job policy remains."}
	}
	return Item{"applied", "CPU-time budget on Agent Job; inherited by new work processes; no hard memory cap."}
}

// Cover the small race between a child starting and initial Agent Job
// attachment. A failed optional assignment must never fail or kill that child.
func ProcessStarted(pid int) {
	if !cpuJob.TryLock() {
		if m := current.Load(); m != nil {
			m.set("cpu", "degraded", "CPU policy update is busy; work starts without waiting for governance.")
		}
		return
	}
	defer cpuJob.Unlock()
	if cpuJob.handle == 0 {
		return
	}
	process, err := gprocess.NewProcess(int32(pid))
	if err != nil {
		return
	}
	parent, err := process.Ppid()
	if err != nil || parent != int32(os.Getpid()) {
		return
	} // PID-reuse boundary.
	handle, err := windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE|windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return
	}
	defer windows.CloseHandle(handle)
	var inside uint32
	ok, _, _ := isProcessInJob.Call(uintptr(handle), uintptr(cpuJob.handle), uintptr(unsafe.Pointer(&inside)))
	if ok != 0 && inside != 0 {
		return
	}
	if err := windows.AssignProcessToJobObject(cpuJob.handle, handle); err != nil {
		if m := current.Load(); m != nil {
			m.set("cpu", "degraded", "A running child could not join the CPU Job; that work continues unrestricted.")
		}
	}
}

func clearCPU(_ context.Context, _ *model.AgentConfig) Item {
	cpuJob.Lock()
	defer cpuJob.Unlock()
	if cpuJob.handle == 0 {
		return Item{"disabled", "CPU governance disabled; no Agent CPU Job was active."}
	}
	info := cpuRateInfo{}
	if _, err := windows.SetInformationJobObject(cpuJob.handle, windows.JobObjectCpuRateControlInformation,
		uintptr(unsafe.Pointer(&info)), uint32(unsafe.Sizeof(info))); err != nil {
		return Item{"degraded", "CPU policy could not be cleared; existing Job policy remains unchanged."}
	}
	return Item{"disabled", "CPU governance disabled; the Agent uses normal host scheduling."}
}
