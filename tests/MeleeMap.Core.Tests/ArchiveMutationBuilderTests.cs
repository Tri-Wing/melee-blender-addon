using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public class ArchiveMutationBuilderTests
{
    [CorpusFact]
    public void PreservesExternalReferenceChainsWithoutAddingRelocations()
    {
        ArchiveLayout? source = null;
        HashSet<int>? internalFields = null;
        HashSet<int>? externalFields = null;
        foreach (string path in Directory.GetFiles(CorpusTests.CorpusDirectory, "*.dat"))
        {
            var candidate = new StageArchive(path).Layout;
            int count = candidate.Read(8);
            var relocations = Enumerable.Range(0, count)
                .Select(index => candidate.Read(32 + candidate.DataSize + index * 4))
                .ToHashSet();
            var external = candidate.Pointers.Keys.Where(field => !relocations.Contains(field))
                .ToHashSet();
            if (external.Count == 0) continue;
            source = candidate; internalFields = relocations; externalFields = external; break;
        }
        Assert.NotNull(source);

        var builder = new ArchiveMutationBuilder(source!);
        builder.AppendZeroed(7);
        var output = new ArchiveLayout(builder.Build());
        int outputCount = output.Read(8);
        var outputRelocations = Enumerable.Range(0, outputCount)
            .Select(index => output.Read(32 + output.DataSize + index * 4)).ToHashSet();
        Assert.Equal(internalFields, outputRelocations);
        Assert.All(externalFields!, field => Assert.DoesNotContain(field, outputRelocations));
        Assert.Equal(source!.References, output.References);
    }

    [PrimaryFixtureFact]
    public void PointerPatchesMaintainRelocationsAndRequireDeclaredSourceOwnership()
    {
        var archive = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrNLa.dat"));
        var identity = ModelIdentity.Capture(archive.Layout, new ModelIdentityCatalog());
        var target = ModelEditing.SelectAll(archive.Layout, identity).First();
        var denied = new ArchiveMutationBuilder(archive.Layout);
        Assert.Equal("ARCHIVE_PATCH_PERMISSION", Assert.Throws<StageException>(() =>
            denied.ClearPointer(target.DobjOffset + 12, "test")).Code);

        var builder = new ArchiveMutationBuilder(archive.Layout);
        builder.PermitSourcePatch(target.DobjOffset + 12, 4, "test");
        builder.ClearPointer(target.DobjOffset + 12, "test");
        int appended = builder.AppendZeroed(8, 4);
        builder.SetPointer(appended, target.PobjOffset, "test");
        var output = new ArchiveLayout(builder.Build());
        Assert.DoesNotContain(target.DobjOffset + 12, output.Pointers.Keys);
        Assert.Equal(target.PobjOffset, output.Pointers[appended]);
        Assert.Contains(builder.PatchLog, patch => patch.Offset == target.DobjOffset + 12
            && patch.Owner == "test");
    }
}
