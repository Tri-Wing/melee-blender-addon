using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public class CollisionTests
{
    private static CollisionRange[] Ranges(int count) => [new(0, count), new(0, 0), new(0, 0), new(0, 0), new(0, 0)];
    private static CollisionData Valid() => new(
        [new(0, 0), new(1, 0), new(2, 0)],
        [new(0, 1, -1, 1, -1, -1, 1, 0), new(1, 2, 0, -1, -1, -1, 1, 0)],
        Ranges(2), [new(Ranges(2), -8, -8, 10, 8, 0, 3)], []);

    [Fact]
    public void AcceptsSimpleConnectedCollision() => Assert.Empty(Valid().Validate(forEditedExport: true));

    [Theory]
    [InlineData("flags", "COLLISION_CATEGORY_FLAGS")]
    [InlineData("bounds", "COLLISION_JOINT_BOUNDS")]
    [InlineData("vertices", "COLLISION_JOINT_VERTICES")]
    [InlineData("joint-range", "COLLISION_RANGE")]
    [InlineData("missing-owner", "COLLISION_JOINT_MISSING")]
    [InlineData("duplicate-owner", "COLLISION_JOINT_OVERLAP")]
    [InlineData("link", "COLLISION_LINK_RECIPROCAL")]
    [InlineData("alternate-link", "COLLISION_LINK")]
    [InlineData("attachment", "COLLISION_ATTACHMENT")]
    public void RejectsInvalidCollision(string mutation, string code)
    {
        var c = Valid();
        switch (mutation)
        {
            case "flags": c.Lines[0] = c.Lines[0] with { HighFlags = 2 }; break;
            case "bounds": c.Joints[0] = c.Joints[0] with { Right = 1 }; break;
            case "vertices": c.Joints[0] = c.Joints[0] with { VertexCount = 4 }; break;
            case "joint-range": c.Joints[0].Ranges[0] = new(0, 3); break;
            case "missing-owner": c.Joints[0].Ranges[0] = new(0, 1); break;
            case "duplicate-owner": c = c with { Joints = [c.Joints[0], c.Joints[0]] }; break;
            case "link": c.Lines[1] = c.Lines[1] with { Previous0 = -1 }; break;
            case "alternate-link": c.Lines[0] = c.Lines[0] with { Next1 = 2 }; break;
            case "attachment": c = c with { Attachments = [new(0, 1, 0, 0)] }; break;
        }
        Assert.Equal(code, Assert.Throws<StageException>(() => c.Validate()).Code);
    }

    [Fact]
    public void ZeroLengthSourceWarnsButEditedExportFails()
    {
        var c = Valid(); c.Vertices[0] = c.Vertices[1];
        Assert.Equal("COLLISION_ZERO_LENGTH", Assert.Single(c.Validate()).Code);
        Assert.Equal("COLLISION_ZERO_LENGTH", Assert.Throws<StageException>(() => c.Validate(forEditedExport: true)).Code);
    }

    [Fact]
    public void StaticCategoryCheckDoesNotGuessDynamicDirection()
    {
        var c = Valid(); c.Ranges[0] = new(0, 0); c.Ranges[4] = new(0, 2);
        c.Joints[0].Ranges[0] = new(0, 0); c.Joints[0].Ranges[4] = new(0, 2);
        c.Lines[0] = c.Lines[0] with { HighFlags = 0 };
        Assert.Empty(c.Validate());
    }

    [Fact]
    public void KeepsBranchedLinksWithoutGuessingReciprocity()
    {
        var c = Valid();
        c = c with { Lines = [c.Lines[0], c.Lines[1] with { Previous0 = -1 }, new(1, 2, 0, -1, -1, -1, 1, 0)], Ranges = Ranges(3) };
        c.Joints[0] = c.Joints[0] with { Ranges = Ranges(3) };
        // Vertex 1 and vertex 2 are now complex; do not impose a unique continuation there.
        Assert.Empty(c.Validate());
    }
}
