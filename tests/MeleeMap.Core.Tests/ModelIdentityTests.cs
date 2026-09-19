using System.Buffers.Binary;
using System.Text;
using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public class ModelIdentityTests
{
    // Two groups, three joints, two DOBJs, two POBJs; all offsets are data-relative.
    private sealed class Fixture
    {
        public const int Groups = 0x30, Root = 0xA0, A = 0xE0, B = 0x120, D0 = 0x160, D1 = 0x170, P0 = 0x180, P1 = 0x198;
        private readonly byte[] data = new byte[0x1B0];
        private readonly Dictionary<int, int> pointers = [];
        public Fixture()
        {
            Set(12, 2); Link(8, Groups); Link(Groups, Root);
            Link(Root + 8, A); Link(A + 12, B);
            Link(A + 16, D0); Link(D0 + 4, D1); Link(D0 + 12, P0); Link(D1 + 12, P1);
        }
        public void Set(int field, int value) => BinaryPrimitives.WriteInt32BigEndian(data.AsSpan(field, 4), value);
        public void Link(int field, int? target)
        {
            if (target.HasValue) pointers[field] = target.Value;
            else pointers.Remove(field);
            Set(field, target ?? 0);
        }
        public ArchiveLayout Build(int shift = 0)
        {
            byte[] name = Encoding.ASCII.GetBytes("map_head\0");
            int dataSize = data.Length + shift;
            byte[] bytes = new byte[32 + dataSize + pointers.Count * 4 + 8 + name.Length];
            void Put(int field, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(field, 4), value);
            Put(0, bytes.Length); Put(4, dataSize); Put(8, pointers.Count); Put(12, 1);
            data.CopyTo(bytes, 32 + shift);
            int reloc = 32 + dataSize;
            foreach (var (field, target) in pointers.OrderBy(p => p.Key))
            {
                Put(32 + shift + field, target + shift); Put(reloc, field + shift); reloc += 4;
            }
            Put(reloc, shift); name.CopyTo(bytes, reloc + 8);
            return new(bytes);
        }
    }

    private static ModelIdentitySnapshot Capture(Fixture f, ModelIdentityCatalog? ids = null) => ModelIdentity.Capture(f.Build(), ids ?? new());
    private static void Error(string code, Fixture f) => Assert.Equal(code, Assert.Throws<StageException>(() => Capture(f)).Code);

    [Fact]
    public void CapturesPreorderAndRenderOwnership()
    {
        var nodes = Capture(new()).Nodes;
        var joints = nodes.Where(n => n.Kind == "jobj").ToArray();
        Assert.Equal(new[] { Fixture.Root, Fixture.A, Fixture.B }, joints.Select(n => n.SourceOffset));
        Assert.Equal(joints[0].Id, joints[1].OwnerId);
        var d0 = nodes.Single(n => n.SourceOffset == Fixture.D0);
        Assert.Equal(joints[1].Id, d0.OwnerId);
        Assert.Equal(d0.Id, nodes.Single(n => n.SourceOffset == Fixture.P0).OwnerId);
        Assert.Equal(2, nodes.Count(n => n.Kind == "group"));
    }

    [Theory]
    [InlineData("reorder")]
    [InlineData("reparent")]
    [InlineData("delete")]
    [InlineData("render-owner")]
    [InlineData("polygon-owner")]
    [InlineData("group-count")]
    [InlineData("group-order")]
    public void RejectsIdentityChangesEvenWhenCountsAreUnchanged(string change)
    {
        var f = new Fixture(); var ids = new ModelIdentityCatalog(); var baseline = Capture(f, ids);
        switch (change)
        {
            case "reorder": f.Link(Fixture.Root + 8, Fixture.B); f.Link(Fixture.B + 12, Fixture.A); f.Link(Fixture.A + 12, null); break;
            case "reparent": f.Link(Fixture.A + 12, null); f.Link(Fixture.A + 8, Fixture.B); break;
            case "delete": f.Link(Fixture.A + 12, null); break;
            case "render-owner": f.Link(Fixture.A + 16, null); f.Link(Fixture.B + 16, Fixture.D0); break;
            case "polygon-owner": f.Link(Fixture.D0 + 12, Fixture.P1); f.Link(Fixture.D1 + 12, Fixture.P0); break;
            case "group-count": f.Set(12, 1); break;
            case "group-order": f.Link(Fixture.Groups, null); f.Link(Fixture.Groups + 0x34, Fixture.Root); break;
        }
        Assert.Equal("MODEL_IDENTITY_CHANGED", Assert.Throws<StageException>(() => baseline.RequireUnchanged(Capture(f, ids))).Code);
    }

    [Fact]
    public void AllowsTransformEditsAndExplicitRelocationWithoutChangingIds()
    {
        var f = new Fixture(); var ids = new ModelIdentityCatalog(); var baseline = Capture(f, ids);
        f.Set(Fixture.A + 0x2C, BitConverter.SingleToInt32Bits(50));
        baseline.RequireUnchanged(Capture(f, ids));
        var moved = ModelIdentity.Capture(f.Build(32), ids.Relocate(locator => locator.Offset + 32));
        baseline.RequireUnchanged(moved);
        Assert.NotEqual(baseline.Nodes[0].SourceOffset, moved.Nodes[0].SourceOffset);
    }

    [Fact]
    public void RejectsJointCycleAndListCycles()
    {
        var f = new Fixture(); f.Link(Fixture.B + 8, Fixture.Root); Error("MODEL_JOBJ_CYCLE", f);
        f = new(); f.Link(Fixture.D1 + 4, Fixture.D0); Error("MODEL_LIST_CYCLE", f);
        f = new(); f.Link(Fixture.P0 + 4, Fixture.P0); Error("MODEL_LIST_CYCLE", f);
    }

    [Fact]
    public void RejectsMissingRelocationsTruncationAndNonfiniteTransforms()
    {
        var f = new Fixture(); f.Set(Fixture.B + 8, Fixture.A); Error("MODEL_POINTER", f);
        f = new(); f.Link(Fixture.B + 8, 0x1AC); Error("MODEL_STRUCTURE", f);
        f = new(); f.Set(Fixture.B + 0x20, BitConverter.SingleToInt32Bits(float.NaN)); Error("MODEL_NONFINITE", f);
    }

    [Fact]
    public void InstancesReferenceExistingJointsWithoutTraversingThemTwice()
    {
        var f = new Fixture(); f.Set(Fixture.B + 4, 1 << 12); f.Link(Fixture.B + 8, Fixture.A);
        var nodes = Capture(f).Nodes;
        Assert.Equal(nodes.Single(n => n.SourceOffset == Fixture.A).Id, nodes.Single(n => n.SourceOffset == Fixture.B).InstanceTargetId);
        f.Link(Fixture.B + 8, null); Error("MODEL_INSTANCE_TARGET", f);
    }

    [Theory]
    [InlineData(1 << 5)]
    [InlineData(1 << 14)]
    public void DoesNotInterpretParticleOrSplinePayloadAsRenderList(int flag)
    {
        var f = new Fixture(); f.Set(Fixture.A + 4, flag); f.Link(Fixture.A + 16, 0x1AC);
        Assert.DoesNotContain(Capture(f).Nodes, n => n.Kind is "dobj" or "pobj");
    }

    [Fact]
    public void PreservesSentinelGroupSlots()
    {
        var f = new Fixture(); f.Set(Fixture.Groups + 0x34, -1);
        Assert.Equal("sentinel-group", Capture(f).Nodes.Last().Kind);
    }
}
