param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("enable", "disable")]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$Name,

    [string]$PrivateIp,
    [Alias("NodeType")]
    [string]$Type = "cpx31",
    [Alias("NodeLocation")]
    [string]$Location = "nbg1",
    [string]$Key = "ml1",
    [string]$TfDir = "infra/terraform/envs/prod",
    [switch]$NoApply
)

$ErrorActionPreference = "Stop"

function Write-Utf8NoBomJson {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Json
    )

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Json, $utf8NoBom)
}

if ($Action -eq "enable" -and [string]::IsNullOrWhiteSpace($PrivateIp)) {
    throw "--PrivateIp is required for enable"
}

$tfvarsFile = Join-Path $TfDir "ml_nodes.auto.tfvars.json"
New-Item -ItemType Directory -Force -Path $TfDir | Out-Null

if ($Action -eq "enable") {
    $payload = [ordered]@{
        ml_nodes = [ordered]@{
            $Key = [ordered]@{
                server_type = $Type
                role        = "k3s-worker"
                private_ip  = $PrivateIp
                node_labels = @("workload=ml", "location=cloud")
                node_taints = @("workload=ml:NoSchedule")
            }
        }
    }
    $json = $payload | ConvertTo-Json -Depth 10
    Write-Utf8NoBomJson -Path $tfvarsFile -Json $json
    Write-Host "Wrote $tfvarsFile with ML node '$Name' (key '$Key')."
}
else {
    $payload = [ordered]@{ ml_nodes = @{} }
    $json = $payload | ConvertTo-Json -Depth 10
    Write-Utf8NoBomJson -Path $tfvarsFile -Json $json
    Write-Host "Wrote $tfvarsFile with ml_nodes disabled for helper-managed node '$Name'."
}

if ($NoApply) {
    Write-Host "Skipping terraform apply (--NoApply set)."
    exit 0
}

$terraformCmd = Get-Command terraform -ErrorAction SilentlyContinue
if (-not $terraformCmd) {
    throw "terraform CLI not found. Run apply manually in $TfDir."
}

Write-Host "Running terraform apply in $TfDir ..."
terraform -chdir="$TfDir" apply -var="location=$Location"
