using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

/// <summary>
/// The stable fixed-camera pose stored in grGroundParam. Normal match camera
/// mode uses separate tuning values and moves dynamically around active subjects.
/// </summary>
public sealed record StageCameraPreview(
    bool FixedCamera,
    bool RuntimeTracksSubjects,
    Vector3Data Position,
    Vector3Data Interest,
    float FieldOfViewDegrees,
    float VerticalAngleDegrees,
    float HorizontalAngleDegrees,
    float NearClip,
    float FarClip);

public static class StageCameraReader
{
    public static StageCameraPreview Read(ArchiveLayout archive)
    {
        var root = archive.Roots.SingleOrDefault(item => item.Name == "grGroundParam");
        Require(root != null, "STAGE_ROOT_MISSING", "Required stage root 'grGroundParam' is missing.");
        var reader = new ArchiveDataReader(archive);
        int offset = root!.Offset;
        reader.Check(offset, 0x68);

        bool fixedCamera = reader.Int(offset + 0x4C) != 0;
        var position = new Vector3Data(
            reader.Float(offset + 0x50), reader.Float(offset + 0x54), reader.Float(offset + 0x58));
        float fieldOfView = reader.Float(offset + 0x5C);
        float vertical = reader.Float(offset + 0x60);
        float horizontal = reader.Float(offset + 0x64);
        Require(Finite(position) && float.IsFinite(fieldOfView)
            && float.IsFinite(vertical) && float.IsFinite(horizontal),
            "STAGE_CAMERA", "Stage camera parameters contain a non-finite value.");
        Require(fieldOfView > 0 && fieldOfView < 180,
            "STAGE_CAMERA", "Stage camera field of view is outside the supported perspective range.");

        double pitch = vertical * Math.PI / 180.0;
        double yaw = horizontal * Math.PI / 180.0;
        // Melee rotates (0, 0, -1) around X, then Y, then Z (which is zero
        // for this descriptor), and aims at that ray's intersection with Z=0.
        var direction = new Vector3Data(
            (float)(-Math.Cos(pitch) * Math.Sin(yaw)),
            (float)Math.Sin(pitch),
            (float)(-Math.Cos(pitch) * Math.Cos(yaw)));
        Require(Math.Abs(direction.Z) > 0.000001f,
            "STAGE_CAMERA", "Stage camera direction is parallel to the gameplay plane.");
        float distance = position.Z / -direction.Z;
        var interest = new Vector3Data(
            position.X + direction.X * distance,
            position.Y + direction.Y * distance,
            0);
        return new(fixedCamera, !fixedCamera, position, interest, fieldOfView,
            vertical, horizontal, 0.1f, 16384.0f);
    }

    private static bool Finite(Vector3Data value) => float.IsFinite(value.X)
        && float.IsFinite(value.Y) && float.IsFinite(value.Z);
}
