using MeleeMap.Core.Gx;

namespace MeleeMap.Core;

public static class CoordinateTransform
{
    public static Vector3Data ToBlender(Vector3Data game) => new(game.X, -game.Z, game.Y);
    public static Vector3Data ToGame(Vector3Data blender) => new(blender.X, blender.Z, -blender.Y);
}
