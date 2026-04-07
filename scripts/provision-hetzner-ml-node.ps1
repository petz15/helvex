param(
    [Parameter(Mandatory = $true)]
    [string]$Name,

    [string]$Type = "cpx31",
    [string]$Image = "ubuntu-24.04",
    [string]$Location = "nbg1",

    [Parameter(Mandatory = $true)]
    [string]$Network,

    [Parameter(Mandatory = $true)]
    [string]$PrivateIp,

    [Parameter(Mandatory = $true)]
    [string]$SshKeyName,

    [string]$SshUser = "ubuntu",

    [Parameter(Mandatory = $true)]
    [string]$CpHost,

    [Parameter(Mandatory = $true)]
    [string]$CpPrivateIp
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command hcloud -ErrorAction SilentlyContinue)) { throw "hcloud CLI not found." }
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) { throw "ssh not found." }
if (-not (Get-Command scp -ErrorAction SilentlyContinue)) { throw "scp not found." }

Write-Host "Creating Hetzner server '$Name'..."
& hcloud server create --name $Name --type $Type --image $Image --location $Location --ssh-key $SshKeyName --output columns=id --no-header | Out-Null

Write-Host "Attaching '$Name' to network '$Network' with IP $PrivateIp..."
& hcloud server add-to-network $Name --network $Network --ip $PrivateIp | Out-Null

$publicIp = (& hcloud server describe $Name --output columns=ipv4 --no-header).Trim()
if ([string]::IsNullOrWhiteSpace($publicIp) -or $publicIp -eq "null") {
    throw "Could not determine public IP for '$Name'."
}

Write-Host "Waiting for SSH on $publicIp..."
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    & ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "$SshUser@$publicIp" "echo ready" *> $null
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 5
}
if (-not $ready) { throw "SSH did not become ready in time." }

Write-Host "Reading join token from control plane..."
$k3sToken = (& ssh $CpHost "sudo cat /var/lib/rancher/k3s/server/node-token").Trim()
if ([string]::IsNullOrWhiteSpace($k3sToken)) {
    throw "Failed to read K3S token from control plane."
}

$scriptDir = Split-Path -Parent $PSCommandPath
$joinScript = Join-Path $scriptDir "join-home-node.sh"
if (-not (Test-Path $joinScript)) {
    throw "join-home-node.sh not found at $joinScript"
}

Write-Host "Copying join script..."
& scp $joinScript "$SshUser@$publicIp:/tmp/join-home-node.sh"
if ($LASTEXITCODE -ne 0) { throw "scp failed." }

Write-Host "Joining node to cluster..."
& ssh "$SshUser@$publicIp" "sudo bash /tmp/join-home-node.sh '$CpPrivateIp' '$k3sToken' '$PrivateIp' '$Name'"
if ($LASTEXITCODE -ne 0) { throw "Remote join command failed." }

Write-Host "Done. Verify on control plane:"
Write-Host "  kubectl get nodes -o wide"
Write-Host "  kubectl describe node $Name | grep -E 'Taints|workload=|location='"
