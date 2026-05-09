param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8790
)

python -m atm_ops_agent.agent api serve --config inventory.atm.toml --host $HostAddress --port $Port
