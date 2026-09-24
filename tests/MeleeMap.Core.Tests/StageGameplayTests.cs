using System.Buffers.Binary;
using System.Text.Json;
using HSDRaw.Melee.Gr;
using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public class StageGameplayTests
{
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase
    };

    [PrimaryFixtureFact]
    public void RecognizesTheTwentyFirstItemSpawnType()
    {
        var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory,
            "GrNLa.dat"));
        var reader = new ArchiveDataReader(source.Layout);
        int mapHead = source.Layout.Roots.Single(root => root.Name == "map_head").Offset;
        int sets = reader.Array(mapHead, reader.Int(mapHead + 4), 12);
        int entries = reader.Array(sets + 4, reader.Int(sets + 8), 4);
        var item = StageGameplayEditing.Read(source.Layout).Points.First(point =>
            point.Kind == "item-spawn");
        byte[] bytes = source.Layout.Bytes.ToArray();
        BinaryPrimitives.WriteInt16BigEndian(
            bytes.AsSpan(32 + entries + item.EntryIndex * 4 + 2, 2), 147);

        var updated = StageGameplayEditing.Read(new ArchiveLayout(bytes));
        var item21 = Assert.Single(updated.Points.Where(point =>
            point.EntryIndex == item.EntryIndex));
        Assert.Equal("item-spawn", item21.Kind);
        Assert.Equal(147, item21.TypeId);
        Assert.True(item21.Editable);
        Assert.Equal(147, (int)PointType.ItemSpawn21);
    }

    [PrimaryFixtureFact]
    public void AddsAndDeletesItemSpawnsWithDeterministicSlotsAndReloadVerification()
    {
        string root = Path.Combine(Path.GetTempPath(),
            "mme-gameplay-topology-" + Guid.NewGuid().ToString("N"));
        string session = Path.Combine(root, "session");
        string output = Path.Combine(root, "out.dat");
        string repeated = Path.Combine(root, "repeated.dat");
        try
        {
            var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory,
                "GrGd.dat"));
            var before = StageGameplayEditing.Read(source.Layout);
            var set = Assert.Single(before.Sets);
            Assert.True(set.ItemSpawnTopologyEditable,
                set.ItemSpawnTopologyReadOnlyReason);
            var items = set.Points.Where(point => point.Kind == "item-spawn")
                .OrderBy(point => point.TypeId).ToArray();
            Assert.NotEmpty(items);
            var deleted = items[1];
            var retainedTypes = items.Where(point => point.Id != deleted.Id)
                .Select(point => point.TypeId).ToHashSet();
            int[] slots = Enumerable.Range(0x7F, 0x15)
                .Where(type => !retainedTypes.Contains(type)).Take(2).ToArray();
            var additions = new[]
            {
                new GameplayItemSpawnAddition(Guid.NewGuid().ToString("N"),
                    set.Index, slots[0], new(12.5f, 34.25f, 0)),
                new GameplayItemSpawnAddition(Guid.NewGuid().ToString("N"),
                    set.Index, slots[1], new(-20, 18, 0)),
            };

            SessionExtractor.Extract(source, session);
            using (var manifest = JsonDocument.Parse(File.ReadAllText(
                       Path.Combine(session, "stage.json"))))
                Assert.True(manifest.RootElement.GetProperty("capabilities")
                    .GetProperty("gameplayItemSpawnTopologyEdit").GetBoolean());
            File.WriteAllText(Path.Combine(session, "edits/gameplay.json"),
                JsonSerializer.Serialize(new GameplayEdits(
                    SessionExtractor.ProtocolVersion, "game", [], additions,
                    [deleted.Id]), Json));

            var result = SessionApplier.Apply(session, output);
            SessionApplier.Apply(session, repeated);
            Assert.True(result.GameplayChanged);
            Assert.Equal(File.ReadAllBytes(output), File.ReadAllBytes(repeated));
            var after = StageGameplayEditing.Read(new StageArchive(output).Layout);
            var actualItems = Assert.Single(after.Sets).Points
                .Where(point => point.Kind == "item-spawn").ToArray();
            Assert.Equal(items.Length + 1, actualItems.Length);
            Assert.DoesNotContain(actualItems, point => point.TypeId == deleted.TypeId
                && point.Position == deleted.Position);
            foreach (var addition in additions)
            {
                var actual = Assert.Single(actualItems.Where(point =>
                    point.TypeId == addition.TypeId));
                Assert.Equal(addition.Position, actual.Position);
                Assert.True(actual.Editable);
            }
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, true);
        }
    }

    [PrimaryFixtureFact]
    public void RejectsForgedItemSpawnTopologyEdits()
    {
        var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory,
            "GrGd.dat"));
        var gameplay = StageGameplayEditing.Read(source.Layout);
        var set = Assert.Single(gameplay.Sets);
        string[] declared = gameplay.EditablePoints.Select(point => point.Id).ToArray();
        var player = gameplay.EditablePoints.First(point =>
            point.Kind == "player-spawn");
        var used = set.Points.Where(point => point.Kind == "item-spawn")
            .Select(point => point.TypeId).ToHashSet();
        int firstFree = Enumerable.Range(0x7F, 0x15).First(type => !used.Contains(type));
        var skipped = new GameplayItemSpawnAddition(Guid.NewGuid().ToString("N"),
            set.Index, firstFree + 1, new(0, 0, 0));

        var exception = Assert.Throws<StageException>(() =>
            StageGameplayEditing.Write(source.Layout, source.Layout,
                new(SessionExtractor.ProtocolVersion, "game", [], [], [player.Id]),
                declared));
        Assert.Equal("GAMEPLAY_DELETE_TARGET", exception.Code);
        exception = Assert.Throws<StageException>(() =>
            StageGameplayEditing.Write(source.Layout, source.Layout,
                new(SessionExtractor.ProtocolVersion, "game", [], [skipped], []),
                declared));
        Assert.Equal("GAMEPLAY_ADDITION_TYPE", exception.Code);
    }

    [PrimaryFixtureFact]
    public void ReadsTypedGeneralPointsAndPublishesStableCapabilities()
    {
        var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory,
            "GrNLa.dat"));
        var gameplay = StageGameplayEditing.Read(source.Layout);
        var set = Assert.Single(gameplay.Sets);
        Assert.Equal(20, set.Points.Length);
        Assert.Equal(20, gameplay.EditablePoints.Length);
        Assert.Equal(2, set.Bounds.Length);
        Assert.All(set.Bounds, bounds => Assert.True(bounds.Editable));
        var p1 = Assert.Single(set.Points.Where(point =>
            point.Kind == "player-spawn" && point.Player == 1));
        Assert.Equal(new Vector3Data(-60, 10, 0), p1.Position);
        var camera = Assert.Single(set.Bounds.Where(bounds =>
            bounds.Kind == "camera-boundary"));
        Assert.Equal(149, set.Points.Single(point =>
            point.Id == camera.FirstPointId).TypeId);
        Assert.Equal(150, set.Points.Single(point =>
            point.Id == camera.SecondPointId).TypeId);

        string session = Path.Combine(Path.GetTempPath(),
            "mme-gameplay-manifest-" + Guid.NewGuid().ToString("N"));
        try
        {
            SessionExtractor.Extract(source, session);
            using var document = JsonDocument.Parse(File.ReadAllText(
                Path.Combine(session, "stage.json")));
            var root = document.RootElement;
            Assert.True(root.GetProperty("capabilities")
                .GetProperty("gameplayPointEdit").GetBoolean());
            Assert.True(root.GetProperty("capabilities")
                .GetProperty("gameplayBoundsEdit").GetBoolean());
            Assert.Equal(20, root.GetProperty("editableGameplayPoints")
                .GetArrayLength());
            Assert.Equal(2, root.GetProperty("gameplay").GetProperty("sets")[0]
                .GetProperty("bounds").GetArrayLength());
        }
        finally
        {
            if (Directory.Exists(session)) Directory.Delete(session, true);
        }
    }

    [PrimaryFixtureFact]
    public void AppliesEveryEditablePointAndPreservesAllOtherBytes()
    {
        string root = Path.Combine(Path.GetTempPath(),
            "mme-gameplay-edit-" + Guid.NewGuid().ToString("N"));
        string session = Path.Combine(root, "session");
        string output = Path.Combine(root, "out.dat");
        try
        {
            var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory,
                "GrNLa.dat"));
            SessionExtractor.Extract(source, session);
            var before = StageGameplayEditing.Read(source.Layout);
            var edits = before.EditablePoints.Select(point => new GameplayPointEdit(
                point.Id, point.Position with
                {
                    X = point.Position.X + 0.5f,
                    Y = point.Position.Y + 0.25f,
                    Z = point.Position.Z + 0.125f
                })).ToArray();
            File.WriteAllText(Path.Combine(session, "edits/gameplay.json"),
                JsonSerializer.Serialize(new GameplayEdits(
                    SessionExtractor.ProtocolVersion, "game", edits, [], []), Json));

            var result = SessionApplier.Apply(session, output);
            Assert.True(result.GameplayChanged);
            Assert.False(result.CollisionChanged);
            Assert.False(result.ModelChanged);
            var written = new StageArchive(output);
            var actual = StageGameplayEditing.Read(written.Layout).Points
                .ToDictionary(point => point.Id);
            Assert.All(edits, edit => Assert.Equal(edit.Position,
                actual[edit.Id].Position));

            var allowed = before.EditablePoints.SelectMany(point =>
                Enumerable.Range(32 + point.SourceOffset + 0x2C, 12)).ToHashSet();
            for (int index = 0; index < source.Layout.Bytes.Length; index++)
                if (!allowed.Contains(index))
                    Assert.Equal(source.Layout.Bytes[index],
                        written.Layout.Bytes[index]);
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, true);
        }
    }

    [PrimaryFixtureFact]
    public void RejectsInvalidBoundsWithoutReplacingAnExistingOutput()
    {
        string root = Path.Combine(Path.GetTempPath(),
            "mme-gameplay-invalid-" + Guid.NewGuid().ToString("N"));
        string session = Path.Combine(root, "session");
        string output = Path.Combine(root, "out.dat");
        try
        {
            var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory,
                "GrNLa.dat"));
            SessionExtractor.Extract(source, session);
            var gameplay = StageGameplayEditing.Read(source.Layout);
            var camera = gameplay.Sets[0].Bounds.Single(bounds =>
                bounds.Kind == "camera-boundary");
            var first = gameplay.Points.Single(point =>
                point.Id == camera.FirstPointId);
            var second = gameplay.Points.Single(point =>
                point.Id == camera.SecondPointId);
            File.WriteAllText(output, "keep me");
            var edit = new GameplayPointEdit(first.Id,
                first.Position with { X = second.Position.X });
            File.WriteAllText(Path.Combine(session, "edits/gameplay.json"),
                JsonSerializer.Serialize(new GameplayEdits(
                    SessionExtractor.ProtocolVersion, "game", [edit], [], []), Json));
            var exception = Assert.Throws<StageException>(() =>
                SessionApplier.Apply(session, output));
            Assert.Equal("GAMEPLAY_BOUNDS_SIZE", exception.Code);
            Assert.Equal("keep me", File.ReadAllText(output));

            edit = new GameplayPointEdit(first.Id,
                first.Position with { X = second.Position.X + 1 });
            File.WriteAllText(Path.Combine(session, "edits/gameplay.json"),
                JsonSerializer.Serialize(new GameplayEdits(
                    SessionExtractor.ProtocolVersion, "game", [edit], [], []), Json));
            exception = Assert.Throws<StageException>(() =>
                SessionApplier.Apply(session, output));
            Assert.Equal("GAMEPLAY_BOUNDS_ORDER", exception.Code);
            Assert.Equal("keep me", File.ReadAllText(output));
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, true);
        }
    }
}
