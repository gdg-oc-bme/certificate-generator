param([string]$PostgresBin = 'C:\Program Files\PostgreSQL\17\bin')
$ErrorActionPreference = 'Stop'
$testRoot = Join-Path $env:TEMP ('gdg-registry-' + [guid]::NewGuid().ToString('N'))
$dataDir = Join-Path $testRoot 'data'
$started = $false
$result = 1
$oldPassFile = $env:PGPASSFILE
try {
    New-Item -ItemType Directory -Path $testRoot | Out-Null
    $env:PGPASSFILE = Join-Path $testRoot 'unused-passfile'
    & "$PostgresBin\initdb.exe" -D $dataDir -U registry_test_owner --auth=trust --no-locale -E UTF8
    if ($LASTEXITCODE -ne 0) { throw 'initdb failed' }
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    & "$PostgresBin\pg_ctl.exe" -D $dataDir -l (Join-Path $testRoot 'postgres.log') -o "-h 127.0.0.1 -p $port" -w start
    if ($LASTEXITCODE -ne 0) { throw 'temporary PostgreSQL startup failed' }
    $started = $true
    $psqlArgs = @('-X', '-w', '-h', '127.0.0.1', '-p', "$port", '-U', 'registry_test_owner', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1')
    & "$PostgresBin\psql.exe" @psqlArgs -f (Join-Path $PSScriptRoot 'bootstrap.sql')
    if ($LASTEXITCODE -ne 0) { throw 'test bootstrap failed' }
    $migrationArgs = @('-X', '-w', '-h', '127.0.0.1', '-p', "$port", '-U', 'registry_migration', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1')
    $migrations = @(Get-ChildItem (Join-Path $PSScriptRoot '../migrations') -Filter '*.sql' | Sort-Object Name)
    if ($migrations.Count -eq 0) { throw 'No registry migrations found' }
    foreach ($migration in $migrations) {
        & "$PostgresBin\psql.exe" @migrationArgs -f $migration.FullName
        Write-Host "Migration $($migration.Name) exit code: $LASTEXITCODE"
        if ($LASTEXITCODE -ne 0) { throw "migration failed: $($migration.Name)" }
        if ($migration.Name -eq '20261109000100_registry_foundation.sql') {
            & "$PostgresBin\psql.exe" @psqlArgs -f (Join-Path $PSScriptRoot 'schema.sql')
            Write-Host "Foundation SQL exit code: $LASTEXITCODE"
            if ($LASTEXITCODE -ne 0) { throw 'foundation schema assertions failed' }
        }
        if ($migration.Name -ne '20261109000100_registry_foundation.sql') {
            & "$PostgresBin\psql.exe" @migrationArgs -f (Join-Path $PSScriptRoot 'migration-role.sql')
            if ($LASTEXITCODE -ne 0) { throw 'migration role boundary assertions failed' }
        }
    }
    # Clone the pristine migrated database: recovery tests validate ALL historical
    # batches, while older negative tests intentionally create incomplete fixtures.
    & "$PostgresBin\psql.exe" @psqlArgs -c 'CREATE DATABASE registry_recovery_tests TEMPLATE postgres'
    if ($LASTEXITCODE -ne 0) { throw 'recovery test database creation failed' }
    & (Join-Path $PSScriptRoot '../../../.venv/Scripts/python.exe') -B (Join-Path $PSScriptRoot 'test_migrations.py') "$PostgresBin\psql.exe" "$port"
    Write-Host "Non-superuser migration tests exit code: $LASTEXITCODE"
    if ($LASTEXITCODE -ne 0) { throw 'non-superuser migration assertions failed' }
    & (Join-Path $PSScriptRoot '../../../.venv/Scripts/python.exe') -B (Join-Path $PSScriptRoot 'test_reservations.py') "$PostgresBin\psql.exe" "$port"
    Write-Host "Reservation tests exit code: $LASTEXITCODE"
    if ($LASTEXITCODE -ne 0) { throw 'reservation assertions failed' }
    & (Join-Path $PSScriptRoot '../../../.venv/Scripts/python.exe') -B (Join-Path $PSScriptRoot 'test_commits.py') "$PostgresBin\psql.exe" "$port"
    Write-Host "Commit tests exit code: $LASTEXITCODE"
    if ($LASTEXITCODE -ne 0) { throw 'commit assertions failed' }
    & (Join-Path $PSScriptRoot '../../../.venv/Scripts/python.exe') -B (Join-Path $PSScriptRoot 'test_edge_rpc.py') "$PostgresBin\psql.exe" "$port"
    Write-Host "RPC tests exit code: $LASTEXITCODE"
    if ($LASTEXITCODE -ne 0) { throw 'Edge RPC assertions failed' }
    & (Join-Path $PSScriptRoot '../../../.venv/Scripts/python.exe') -B (Join-Path $PSScriptRoot 'test_certificate_scope.py') "$PostgresBin\psql.exe" "$port"
    Write-Host "Certificate scope tests exit code: $LASTEXITCODE"
    if ($LASTEXITCODE -ne 0) { throw 'certificate scope assertions failed' }
    & (Join-Path $PSScriptRoot '../../../.venv/Scripts/python.exe') -B (Join-Path $PSScriptRoot 'test_review_regressions.py') "$PostgresBin\psql.exe" "$port"
    Write-Host "Review regression tests exit code: $LASTEXITCODE"
    if ($LASTEXITCODE -ne 0) { throw 'review regression assertions failed' }
    & node (Join-Path $PSScriptRoot '../functions/registry-v1/api.integration.test.mjs') "$PostgresBin\psql.exe" "$port"
    Write-Host "Handler/SQL integration tests exit code: $LASTEXITCODE"
    if ($LASTEXITCODE -ne 0) { throw 'handler/SQL integration assertions failed' }
    & (Join-Path $PSScriptRoot '../../../.venv/Scripts/python.exe') -B (Join-Path $PSScriptRoot 'test_recovery.py') "$PostgresBin\psql.exe" "$port" 'registry_recovery_tests'
    Write-Host "Recovery tests exit code: $LASTEXITCODE"
    if ($LASTEXITCODE -ne 0) { throw 'recovery assertions failed' }
    $result = 0
} catch { Write-Error -ErrorAction Continue $_ } finally {
    if ($started) {
        & "$PostgresBin\pg_ctl.exe" -D $dataDir -m fast -w stop
        if ($LASTEXITCODE -ne 0) { $result = 1 }
    }
    $env:PGPASSFILE = $oldPassFile
    Write-Host "Disposable cluster retained for inspection: $testRoot"
}
Write-Host "Database test exit code: $result"
exit $result
