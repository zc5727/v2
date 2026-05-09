# ATM Agent Skill LangGraph

```mermaid
flowchart TD
    A["User prompt"] --> B["Intent parse / planner"]
    B --> C{"Builtin ATM skill?"}
    C -- "clickhouse-table-check" --> D["Validate params<br/>table / time_column / days"]
    C -- "frontend-rollback-preview" --> E["Validate params<br/>release_name / base_dir"]
    C -- "other builtin skill" --> F["Route to existing skill builder"]
    C -- "custom skill" --> G["Route to custom skill builder"]

    D --> H["Risk gate<br/>readonly"]
    E --> H
    F --> I["Risk gate<br/>readonly or mutating"]
    G --> I

    H --> J["Render remote command"]
    I --> J
    J --> K["SSH execute or preview"]
    K --> L["Summarize result"]
    L --> M["Archive incident / knowledge"]

    D --> N["Missing params response"]
    E --> N
```

## Notes

- `clickhouse-table-check` is a read-only verification skill.
- `frontend-rollback-preview` is a read-only rollback precheck skill.
- Mutating rollback remains a separate builtin action: `frontend-rollback`.
