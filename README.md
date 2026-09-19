# Melee Map Editor

Linux-first Blender stage editor, with a working Blender collision editing preview. See
[the implementation plan](docs/blender-addon-plan.md) and
[format audit / implementation status](docs/format-audit.md).

Requires the .NET 8 SDK. The CLI references `HSDLib/HSDRaw` directly; it does not
reference HSDRawViewer or launch a GUI. NuGet access is needed for initial restore.

```sh
dotnet build MeleeMap.sln
dotnet run --project src/MeleeMap.Cli -- inspect example_assets/GrNLa.dat --json
dotnet run --project src/MeleeMap.Cli -- validate example_assets/GrNLa.dat --json
dotnet run --project src/MeleeMap.Cli -- roundtrip example_assets/GrNLa.dat --output /tmp/GrNLa-edited.dat --compare
dotnet run --project src/MeleeMap.Cli -- extract example_assets/GrNLa.dat --session /tmp/GrNLa-session
dotnet run --project src/MeleeMap.Cli -- apply /tmp/GrNLa-session --output /tmp/GrNLa-applied.dat
dotnet test MeleeMap.sln
```

Blender export defaults to the imported filename and confirms overwrites.
`apply` validates a temporary DAT before replacing an existing output; failed
validation leaves that output intact. Outputs must remain outside the session.
The separate `roundtrip` diagnostic still requires a new filename and can compare
graph/payload semantics before publishing. Use `--compare` for preservation testing.

All commands write a versioned JSON envelope to stdout: `protocolVersion`, `ok`,
and either `command`/`data` or `error` with stable `code` and `message` fields.
Failures also write a diagnostic to stderr and exit with status 1. JSON is the
default, even without `--json`. Root offsets are relative to the DAT data section.

Tests discover local `.dat` files in `example_assets`, or in the directory named
by `MELEEMAP_CORPUS`. The corpus test is explicitly skipped if that directory is
absent. Game assets are not included in release packages. Synthetic format and
CLI tests run without assets.

Implemented: inventory, model hierarchy/list validation and identity comparison,
collision consistency validation, preservation round-trips, full `GrNLa` geometry
extraction, and static collision `apply`. `GrNLa` extracts all 93 polygon objects
(13,597 triangles), with a static joint/envelope preview in Blender.
See [Blender installation and editing instructions](docs/blender-addon.md).

Sessions now use protocol v2; re-extract old v1 sessions. Put collision changes in
`edits/collision.json`; `apply` with no edits produces a byte-identical DAT. Source
and baseline hashes, protected model identities, and output validation gate each
export. See [session protocol](docs/session-protocol.md) for an edit example and
supported operations.

`validate` returns source warnings for retail zero-length lines and attachments
that require companion archives. Those files remain eligible for preservation
round-trips. Newly edited collision will reject zero-length lines.

The Blender 4.5.0 add-on imports grey models, edits static collision positions
and properties, provides split/extend/connect/reverse tools with native deletion,
and exports through the CLI. All structurally supported rigid meshes support vertex
edits that preserve source appearance, plus topology edits with reusable stage materials and
Blender UV maps (or grey export), including multiple targets per export. Supported
stage materials now include packed texture previews and UV Editor images. Dynamic collision editing, full
GX/reference validation, and self-contained
distribution are still pending. Passing the
current validator is not an in-game compatibility test.

The user has reported all existing collision features working. Single-target model
editing and joined cubes also work in game. Multi-target editing also works in game. Appearance-preserving vertex editing
also works in game. New-geometry material/UV export has also been confirmed in game. Texture previews
pass automated decode, render, and Blender save/reopen checks. Re-import into a new scene to enable all eligible meshes; see
[the model editing workflow](docs/blender-addon.md#edit-the-supported-model).
