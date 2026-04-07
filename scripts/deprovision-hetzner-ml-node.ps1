param(
    [Parameter(Mandatory = $true)]
    [string]$Name,

    [string]$CpHost,
    [switch]$SkipDrain,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command hcloud -ErrorAction SilentlyContinue)) { throw "hcloud CLI not found." }
if (-not $SkipDrain -and [string]::IsNullOrWhiteSpace($CpHost)) {
    throw "--CpHost is required unless --SkipDrain is set."
}

if (-not $Yes) {
    Write-Host "About to deprovision node '$Name'."
    Write-Host "This will remove workloads and then delete the Hetzner server."
    $answer = Read-Host "Continue? [y/N]"
    if ($answer -notin @("y", "Y")) {
        Write-Host "Cancelled."
        exit 0
    }
}

if (-not $SkipDrain) {
    Write-Host "Cordoning and draining Kubernetes node '$Name'..."
    & ssh $CpHost "kubectl cordon '$Name' || true"
    & ssh $CpHost "kubectl drain '$Name' --ignore-daemonsets --delete-emptydir-data --force --grace-period=60 --timeout=10m || true"
    & ssh $CpHost "kubectl delete node '$Name' || true"
}

Write-Host "Deleting Hetzner server '$Name'..."
& hcloud server delete $Name

Write-Host "Done."
if (-not $SkipDrain) {
    Write-Host "Verify remaining nodes:"
    Write-Host "  ssh $CpHost \"kubectl get nodes -o wide\""
}
