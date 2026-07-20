<#
.SYNOPSIS
  Push the current branch to ALL three AetherSeed mirrors, individually.
.DESCRIPTION
  Adds any missing remotes first, then pushes to each separately so one failing
  remote does not block the others.
.EXAMPLE
  ./scripts/push_all.ps1
  ./scripts/push_all.ps1 -Branch main
#>
param([string]$Branch)

$ErrorActionPreference = "Continue"

$remotes = [ordered]@{
  an3s      = "https://github.com/AN3S-CREATE/Vralogix-AetherSeed-OSINT.git"
  veralogix = "https://github.com/veralogix-group-innovation/Vralogix-AetherSeed-OSINT.git"
  catalyst  = "https://github.com/VeralogixCatalyst/Vralogix-AetherSeed-OSINT.git"
}

if (-not $Branch) { $Branch = (git rev-parse --abbrev-ref HEAD).Trim() }
Write-Host "Pushing branch '$Branch' to all mirrors..."

$failed = @()
foreach ($name in $remotes.Keys) {
  $url = $remotes[$name]
  git remote get-url $name *> $null
  if ($LASTEXITCODE -eq 0) { git remote set-url $name $url } else { git remote add $name $url }

  Write-Host "  -> $name ($url)"
  git push $name $Branch
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "    push to '$name' FAILED"
    $failed += $name
  }
}

if ($failed.Count -ne 0) {
  Write-Error "Completed with failures: $($failed -join ', ')"
  exit 1
}
Write-Host "All mirrors updated."
