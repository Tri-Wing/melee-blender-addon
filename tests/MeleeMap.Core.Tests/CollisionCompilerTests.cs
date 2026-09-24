using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public class CollisionCompilerTests
{
    private static CollisionRange[] Ranges(int count) => [new(0, count), new(0, 0), new(0, 0), new(0, 0), new(0, 0)];
    private static string Id() => Guid.NewGuid().ToString("N");
    private static (CollisionData Source, CollisionSourceIds Ids, CollisionEdits Edits) Fixture()
    {
        var source = new CollisionData([new(0, 0), new(1, 0), new(2, 0)],
            [new(0, 1, -1, 1, -1, -1, 1, 0), new(1, 2, 0, -1, -1, -1, 1, 0)],
            Ranges(2), [new(Ranges(2), -8, -8, 10, 8, 0, 3)], []);
        var ids = new CollisionSourceIds([Id(), Id(), Id()], [Id(), Id()], [Id()]);
        var edit = new CollisionEdits(SessionExtractor.ProtocolVersion, "game",
            source.Vertices.Select((v, i) => new CollisionEditVertex(ids.Vertices[i], v.X, v.Y)).ToArray(),
            source.Lines.Select((l, i) => new CollisionEditLine(ids.Lines[i], ids.Vertices[l.Vertex0], ids.Vertices[l.Vertex1], ids.Joints[0], "floor", l.HighFlags, l.LowFlags)).ToArray());
        return (source, ids, edit);
    }

    [Fact]
    public void WeldsNearEndpointsAndRebuildsLinks()
    {
        var (source, ids, edit) = Fixture(); string extra = Id();
        edit = edit with { Vertices = [.. edit.Vertices, new(extra, 1.00001f, 0)] };
        edit.Lines[1] = edit.Lines[1] with { Vertex0Id = extra };
        var result = CollisionCompiler.Compile(source, ids, edit);
        Assert.Equal(3, result.Vertices.Length); Assert.Equal(1, result.Lines[0].Next0); Assert.Equal(0, result.Lines[1].Previous0);
        Assert.Equal(source.Joints[0].Left, result.Joints[0].Left); Assert.Equal(source.Joints[0].Right, result.Joints[0].Right);
    }

    [Fact]
    public void KeepsThenDropsAlternateLinksAsEndpointsChange()
    {
        var (source, ids, edit) = Fixture(); source.Lines[0] = source.Lines[0] with { Next1 = 1 };
        Assert.Equal(1, CollisionCompiler.Compile(source, ids, edit).Lines[0].Next1);
        edit.Vertices[1] = edit.Vertices[1] with { Y = 1 };
        Assert.Equal(-1, CollisionCompiler.Compile(source, ids, edit).Lines[0].Next1);
    }

    [Theory]
    [InlineData("floor", 2, 1)]
    [InlineData("ceiling", -2, 1)]
    [InlineData("right-wall", 1, -2)]
    [InlineData("left-wall", 1, 2)]
    public void EnforcesFacingForEachCategory(string category, float dx, float dy)
    {
        var (source, ids, edit) = Fixture();
        for (int i = 0; i < edit.Vertices.Length; i++)
            edit.Vertices[i] = edit.Vertices[i] with { X = i * dx, Y = i * dy };
        for (int i = 0; i < edit.Lines.Length; i++)
            edit.Lines[i] = edit.Lines[i] with { Category = category };
        Assert.Equal(2, CollisionCompiler.Compile(source, ids, edit).Lines.Length);
        for (int i = 0; i < edit.Lines.Length; i++)
            edit.Lines[i] = edit.Lines[i] with { Vertex0Id = edit.Lines[i].Vertex1Id, Vertex1Id = edit.Lines[i].Vertex0Id };
        Assert.Equal("COLLISION_FACING", Assert.Throws<StageException>(() => CollisionCompiler.Compile(source, ids, edit)).Code);
    }

    [Fact]
    public void CeilingNeedsReversedEndpointsAndRebuiltLinks()
    {
        var (source, ids, edit) = Fixture();
        for (int i = 0; i < edit.Lines.Length; i++)
            edit.Lines[i] = edit.Lines[i] with { Category = "ceiling" };
        Assert.Equal("COLLISION_FACING", Assert.Throws<StageException>(() => CollisionCompiler.Compile(source, ids, edit)).Code);
        for (int i = 0; i < edit.Lines.Length; i++)
            edit.Lines[i] = edit.Lines[i] with { Vertex0Id = edit.Lines[i].Vertex1Id, Vertex1Id = edit.Lines[i].Vertex0Id };
        var result = CollisionCompiler.Compile(source, ids, edit);
        Assert.Equal(2, result.Ranges[1].Count);
        Assert.All(result.Lines, line => Assert.True(result.Vertices[line.Vertex0].X > result.Vertices[line.Vertex1].X));
        Assert.Equal(1, result.Lines[0].Previous0);
        Assert.Equal(0, result.Lines[1].Next0);
    }

    [Theory]
    [InlineData("zero", "COLLISION_ZERO_LENGTH")]
    [InlineData("flags", "COLLISION_UNKNOWN_FLAGS")]
    [InlineData("membership", "COLLISION_DYNAMIC_MEMBERSHIP")]
    [InlineData("joint", "COLLISION_JOINT_ID")]
    [InlineData("duplicate", "COLLISION_ID")]
    [InlineData("nonfinite", "COLLISION_NONFINITE")]
    public void RejectsUnsafeEdits(string mutation, string code)
    {
        var (source, ids, edit) = Fixture();
        switch (mutation)
        {
            case "zero": edit.Vertices[0] = edit.Vertices[0] with { X = 1 }; break;
            case "flags": edit.Lines[0] = edit.Lines[0] with { HighFlags = 128 }; break;
            case "membership": edit.Lines[0] = edit.Lines[0] with { Category = "dynamic" }; break;
            case "joint": edit.Lines[0] = edit.Lines[0] with { JointId = Id() }; break;
            case "duplicate": edit.Lines[1] = edit.Lines[0]; break;
            case "nonfinite": edit.Vertices[0] = edit.Vertices[0] with { X = float.PositiveInfinity }; break;
        }
        Assert.Equal(code, Assert.Throws<StageException>(() => CollisionCompiler.Compile(source, ids, edit)).Code);
    }

    [Fact]
    public void RebuildsDynamicRangeAndPreservesAttachments()
    {
        var (source, ids, edit) = Fixture();
        source = source with
        {
            Ranges = [new(0, 1), new(0, 0), new(0, 0), new(0, 0), new(1, 1)],
            Joints = [source.Joints[0] with
            {
                Ranges = [new(0, 1), new(0, 0), new(0, 0), new(0, 0), new(1, 1)]
            }],
            Lines = [source.Lines[0], source.Lines[1] with { HighFlags = 0x11 }],
            Attachments = [new(2, 0, -1, 3)]
        };
        edit.Lines[1] = edit.Lines[1] with { Category = "dynamic", HighFlags = 0x11 };
        edit.Vertices[2] = edit.Vertices[2] with { Y = 0.5f };
        string extraVertex = Id();
        edit = edit with
        {
            Vertices = [.. edit.Vertices, new(extraVertex, 3, 0.5f)],
            Lines = [.. edit.Lines, new(Id(), ids.Vertices[2], extraVertex,
                ids.Joints[0], "dynamic", 0x11, 0)]
        };

        var result = CollisionCompiler.Compile(source, ids, edit);

        Assert.Equal(new CollisionRange(1, 2), result.Ranges[4]);
        Assert.Equal(new CollisionRange(1, 2), result.Joints[0].Ranges[4]);
        Assert.Equal((ushort)0x11, result.Lines[1].HighFlags);
        Assert.Equal((ushort)0x11, result.Lines[2].HighFlags);
        Assert.Equal(source.Attachments, result.Attachments);
        Assert.Empty(result.Validate(forEditedExport: true));
    }
}
