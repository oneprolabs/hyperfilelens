import { describe, expect, it } from 'vitest'

import {
  buildLocalServiceCommand,
  buildLocalUninstallCommand,
  buildLocalUpgradeCommand,
  defaultPackagePath,
} from './nodeInstallCommands'

describe('node upgrade download TLS policy', () => {
  it('uses strict TLS for SaaS Gateway upgrades', () => {
    const command = buildLocalUpgradeCommand(
      'linux',
      '/tmp/hfl-agent.tar.gz',
      true,
      'https://hyperfilelens.com/agent.tar.gz',
      'gateway',
      true,
    )

    expect(command).toContain("curl --proto '=https' --tlsv1.2 -fL")
    expect(command).not.toContain('curl -k')
  })

  it('retains the explicit self-hosted TLS bypass', () => {
    const command = buildLocalUpgradeCommand(
      'linux',
      '/tmp/hfl-agent.tar.gz',
      true,
      'https://hfl.localhost/agent.tar.gz',
      'gateway',
      false,
    )

    expect(command).toContain('curl -k -fL')
  })

  it('does not add -k to strict Windows downloads', () => {
    const command = buildLocalUpgradeCommand(
      'windows',
      'C:\\Temp\\hfl-agent.zip',
      true,
      'https://hyperfilelens.com/agent.zip',
      'agent',
      true,
    )

    expect(command).toContain('curl.exe -fL')
    expect(command).not.toContain('curl.exe -k')
  })
})

describe('manual node maintenance commands', () => {
  it('uses administrator installation for specified-user continuous mode', () => {
    const command = buildLocalUpgradeCommand(
      'linux', '/tmp/hfl-agent.tar.gz', true,
      'https://console.example/hfl-agent.tar.gz', 'agent', true, '', 'amd64', 'account',
    )
    expect(command).toContain('sudo ')
    expect(command).not.toContain('.local/lib/hyperfilelens-agent')
  })
  it('uses the node architecture in the downloaded package path', () => {
    expect(defaultPackagePath('linux', '1.0.1', 'arm64')).toContain('linux-arm64')
    expect(defaultPackagePath('macos', '1.0.1', 'amd64')).toContain('darwin-amd64')
    expect(defaultPackagePath('windows', '1.0.1', 'amd64')).toBe(
      '$env:TEMP\\hfl-agent-1.0.1-windows-amd64.zip',
    )
  })

  it('restarts both Data Gateway services', () => {
    const command = buildLocalServiceCommand('linux', 'restart', 'gateway')

    expect(command).toContain('/opt/hyperfilelens-agent/install.sh restart')
    expect(command).toContain('docker compose -p hyperfilelens-gateway')
    expect(command).toContain('up -d')
  })

  it('downloads a current helper before upgrading a Data Gateway', () => {
    const command = buildLocalUpgradeCommand(
      'linux',
      '/tmp/hfl-agent.tar.gz',
      true,
      'https://console.example/media/agent-releases/1.0.1/agent.tar.gz?t=signed',
      'gateway',
      true,
      '',
      'arm64',
    )

    expect(command).toContain('/media/enroll-bootstrap/hfl-enroll-linux-arm64')
    expect(command).toContain('mktemp /tmp/hfl-enroll.')
    expect(command).toContain('gateway-upgrade --from /tmp/hfl-agent.tar.gz')
    expect(command).not.toContain('sudo hfl-enroll')
  })

  it('stops the LensNode sidecar before the Gateway Agent', () => {
    const command = buildLocalServiceCommand('linux', 'stop', 'gateway')

    expect(command.indexOf('docker compose')).toBeLessThan(command.indexOf('install.sh stop'))
  })

  it('preserves local data unless purge is explicitly selected', () => {
    expect(buildLocalUninstallCommand('linux', false, 'agent')).not.toContain('--purge-all')
    expect(buildLocalUninstallCommand('linux', false, 'gateway')).toBe(
      'sudo /opt/hyperfilelens-agent/install.sh uninstall',
    )
  })

  it.each([
    {
      os: 'linux' as const,
      installScript: '"$HOME/.local/lib/hyperfilelens-agent/install.sh"',
    },
    {
      os: 'macos' as const,
      installScript: '"$HOME/Library/Application Support/HyperFileLens/Agent/bin/install.sh"',
    },
  ])('keeps $os user-level maintenance inside the user install', ({ os, installScript }) => {
    const upgrade = buildLocalUpgradeCommand(
      os,
      `/tmp/hfl-agent-${os}.tar.gz`,
      true,
      `https://console.example/hfl-agent-${os}.tar.gz`,
      'agent',
      true,
      '',
      'amd64',
      'user',
    )
    const uninstall = buildLocalUninstallCommand(os, true, 'agent', 'user')
    const service = buildLocalServiceCommand(os, 'restart', 'agent', 'user')

    expect(upgrade).toContain(`${installScript} upgrade`)
    expect(uninstall).toContain(`${installScript} uninstall --purge-all`)
    expect(service).toBe(`${installScript} restart`)
    expect(`${upgrade}\n${uninstall}\n${service}`).not.toContain('sudo')
  })

  it('uses the current-user Windows install without elevation', () => {
    const upgrade = buildLocalUpgradeCommand(
      'windows',
      '$env:TEMP\\hfl-agent.zip',
      true,
      'https://console.example/hfl-agent.zip',
      'agent',
      true,
      '',
      'amd64',
      'user',
    )
    const uninstall = buildLocalUninstallCommand('windows', true, 'agent', 'user')
    const service = buildLocalServiceCommand('windows', 'restart', 'agent', 'user')

    for (const command of [upgrade, uninstall, service]) {
      expect(command).toContain('$env:LOCALAPPDATA\\Programs\\HyperFileLens\\Agent\\install.cmd')
      expect(command).not.toContain('$env:ProgramFiles')
      expect(command).not.toContain('Start-Service')
      expect(command).not.toContain('Restart-Service')
    }
    expect(upgrade).toContain('-o "$env:TEMP\\hfl-agent.zip"')
  })

  it('uses the Task Scheduler installer for Windows specified-user mode', () => {
    const commands = [
      buildLocalServiceCommand('windows', 'status', 'agent', 'account'),
      buildLocalServiceCommand('windows', 'start', 'agent', 'account'),
      buildLocalServiceCommand('windows', 'stop', 'agent', 'account'),
      buildLocalServiceCommand('windows', 'restart', 'agent', 'account'),
    ]

    expect(commands.every((command) => command.includes('$env:ProgramFiles\\HyperFileLens\\Agent\\install.cmd'))).toBe(true)
    expect(commands.join('\n')).not.toContain('Start-Service')
    expect(commands.join('\n')).not.toContain('Restart-Service')
  })
})
