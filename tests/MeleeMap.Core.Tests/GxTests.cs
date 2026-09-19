using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public class GxTests
{
    [Fact]
    public void AxisConversionRoundTripsAndPreservesHandedness()
    {
        var point = new Vector3Data(12.5f, -9, 3.25f);
        Assert.Equal(new Vector3Data(12.5f, -3.25f, -9), CoordinateTransform.ToBlender(point));
        Assert.Equal(point, CoordinateTransform.ToGame(CoordinateTransform.ToBlender(point)));
        var x = CoordinateTransform.ToBlender(new(1, 0, 0)); var y = CoordinateTransform.ToBlender(new(0, 1, 0));
        var cross = new Vector3Data(x.Y * y.Z - x.Z * y.Y, x.Z * y.X - x.X * y.Z, x.X * y.Y - x.Y * y.X);
        Assert.Equal(CoordinateTransform.ToBlender(new(0, 0, 1)), cross);
    }

    [Theory]
    [InlineData(0x90, 6, new[] { 0, 1, 2, 3, 4, 5 })]
    [InlineData(0x80, 4, new[] { 0, 1, 2, 0, 2, 3 })]
    [InlineData(0x98, 5, new[] { 0, 1, 2, 2, 1, 3, 2, 3, 4 })]
    [InlineData(0xA0, 5, new[] { 0, 1, 2, 0, 2, 3, 0, 3, 4 })]
    public void TriangulatesWithCorrectWinding(int primitive, int count, int[] indices) =>
        Assert.Equal(indices.Select(i => i + 10), GxMeshDecoder.Triangulate(primitive, 10, count));

    [Theory]
    [InlineData(0x90, 4)]
    [InlineData(0x80, 6)]
    [InlineData(0x98, 2)]
    public void RejectsIncompletePrimitives(int primitive, int count) =>
        Assert.Equal("GX_PRIMITIVE_COUNT", Assert.Throws<StageException>(() => GxMeshDecoder.Triangulate(primitive, 0, count)).Code);
}
