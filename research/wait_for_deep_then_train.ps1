$ErrorActionPreference = "Stop"
$ResearchDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ResearchDir

$Required = @(
    "deep/KXBTC15M.json", "deep/KXETH15M.json", "deep/KXSOL15M.json",
    "deep/KXXRP15M.json", "deep/KXDOGE15M.json",
    "deep/spot_BTC-USD.json", "deep/spot_ETH-USD.json", "deep/spot_SOL-USD.json",
    "deep/spot_XRP-USD.json", "deep/spot_DOGE-USD.json"
)

$PreviousSizes = $null
$StablePasses = 0
while ($true) {
    $Missing = @($Required | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
    if ($Missing.Count -gt 0) {
        $PreviousSizes = $null
        $StablePasses = 0
        Start-Sleep -Seconds 30
        continue
    }
    $CurrentSizes = @($Required | ForEach-Object { (Get-Item -LiteralPath $_).Length })
    $AllNonzero = @($CurrentSizes | Where-Object { $_ -le 0 }).Count -eq 0
    $SameAsPrevious = $null -ne $PreviousSizes -and
        (Compare-Object -ReferenceObject $PreviousSizes -DifferenceObject $CurrentSizes).Count -eq 0
    if ($AllNonzero -and $SameAsPrevious) {
        $StablePasses += 1
    } else {
        $StablePasses = 0
    }
    if ($StablePasses -ge 2) {
        break
    }
    $PreviousSizes = $CurrentSizes
    Start-Sleep -Seconds 30
}

"[$(Get-Date -Format o)] deep data complete; starting train-only studies" |
    Out-File -LiteralPath "volatility_train_run.log" -Encoding utf8

python s7_vol_retest.py 2>&1 | Tee-Object -FilePath "volatility_train_run.log" -Append
if ($LASTEXITCODE -ne 0) { throw "s7_vol_retest.py failed with exit $LASTEXITCODE" }

python s8_vol_estimators.py 2>&1 | Tee-Object -FilePath "volatility_train_run.log" -Append
if ($LASTEXITCODE -ne 0) { throw "s8_vol_estimators.py failed with exit $LASTEXITCODE" }

python s9_vol_cost_filters.py 2>&1 | Tee-Object -FilePath "volatility_train_run.log" -Append
if ($LASTEXITCODE -ne 0) { throw "s9_vol_cost_filters.py failed with exit $LASTEXITCODE" }

"[$(Get-Date -Format o)] train-only studies complete; held-out evaluator was NOT run" |
    Out-File -LiteralPath "volatility_train_run.log" -Encoding utf8 -Append
