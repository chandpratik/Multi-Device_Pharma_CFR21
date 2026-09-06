<#
.SYNOPSIS
    Protect the Part 3 signing key and external audit anchor.

.DESCRIPTION
    Applies explicit NTFS ACLs to the two protected files used by the audit
    trail. Run this after the application has created both files and under an
    elevated PowerShell session. The script never walks the data directory and
    never changes unrelated files.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DataDirectory,

    [Parameter(Mandatory = $true)]
    [string]$ServiceAccount
)

$resolvedDirectory = (Resolve-Path -LiteralPath $DataDirectory -ErrorAction Stop).Path
$targets = @(
    (Join-Path $resolvedDirectory "audit_signing.key"),
    (Join-Path $resolvedDirectory "audit_anchor.json")
)

foreach ($target in $targets) {
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
        throw "Protected audit artifact not found: $target"
    }
}

foreach ($target in $targets) {
    & icacls.exe $target /inheritance:r | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Could not remove inherited ACLs from $target"
    }

    & icacls.exe $target /grant:r "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" "$($ServiceAccount):(M)" | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Could not apply protected ACLs to $target"
    }
}

Write-Output "Protected audit signing key and external anchor in $resolvedDirectory"
