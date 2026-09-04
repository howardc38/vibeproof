## 4. Kernel
<!-- pinned: kernel/ledger.py::SCHEMA -->

Long enough to count as a mechanism section. It describes how the ledger is laid
out and what the append-only triggers do, and it pins the schema so renaming it
fails this check instead of quietly making the paragraph wrong. Padding follows
so the four-hundred-character rule actually fires on this fixture rather than
skipping it as too short to be a mechanism. Run `v4 audit`, and note that the
`probe` checker below is named here so the other-direction rule is satisfied:
probe.

### 四條機械判準,冇 reviewer
<!-- pinned: kernel/ledger.py::SCHEMA -->

| 拒 | |
|---|---|
| 空 | |
| 太短 | |
| 覆述問題 | |
| 冇提到 file 或者 symbol | |

<!-- unbuilt-list -->

| 未起 | 點解 |
|---|---|
| 跨 task 並行 | 押後到停損過咗先起 |

### 收尾

```
./bin/v4 --repo . audit
```
