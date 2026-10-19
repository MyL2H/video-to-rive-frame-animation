#!/usr/bin/env python3
"""Build a multi-action Rive state machine with whole-second interruption returns."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import quoteattr

from PIL import Image


NUMERIC_SUFFIX = re.compile(r"(\d+)(?=\.[^.]+$)")


@dataclass(frozen=True)
class FrameRef:
    path: Path
    asset_id: int
    component_id: int


@dataclass(frozen=True)
class ReturnClip:
    label: str
    boundary_frame: int
    frames: list[FrameRef]
    fps: float
    duration_frames: int


@dataclass(frozen=True)
class Action:
    name: str
    fps: float
    frames: list[FrameRef]
    returns: list[ReturnClip]


def frame_number(path: Path) -> int:
    match = NUMERIC_SUFFIX.search(path.name)
    if not match:
        raise ValueError(f"Frame filename has no numeric suffix: {path}")
    return int(match.group(1))


def ordered_frames(directory: Path, pattern: str) -> list[Path]:
    frames = sorted(directory.glob(pattern), key=frame_number)
    if not frames:
        raise ValueError(f"No frames matched {pattern!r} in {directory}")
    numbers = [frame_number(path) for path in frames]
    if numbers != list(range(len(frames))):
        raise ValueError(f"Frame numbering is not continuous from 0 in {directory}")
    return frames


def frame_dimensions(paths: list[Path]) -> tuple[int, int]:
    with Image.open(paths[0]) as first:
        size = first.size
        mode = first.mode
    if mode != "RGBA":
        raise ValueError(f"Frames must be RGBA, found {mode}: {paths[0]}")
    for path in paths[1:]:
        with Image.open(path) as frame:
            if frame.size != size or frame.mode != "RGBA":
                raise ValueError(f"Frame size or mode mismatch: {path}")
    return size


def number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def resolve(project_manifest: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (project_manifest.parent / path).resolve()


def make_frames(
    paths: list[Path], project_dir: Path, next_component_id: int
) -> tuple[list[FrameRef], int]:
    refs = []
    component_id = next_component_id
    for path in paths:
        asset_id = component_id + 1
        refs.append(FrameRef(path, asset_id, component_id))
        component_id += 2
    return refs, component_id


def load_actions(manifest_path: Path) -> tuple[list[Action], dict, tuple[int, int]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    project_dir = manifest_path.parent
    pattern = manifest.get("framePattern", "*.webp")
    all_paths: list[Path] = []
    next_component_id = 10_000
    actions = []

    for action_data in manifest.get("actions", []):
        fps = float(action_data["fps"])
        if fps <= 0:
            raise ValueError(f"Action FPS must be positive: {action_data.get('name')}")
        source_paths = ordered_frames(resolve(manifest_path, action_data["framesDir"]), pattern)
        source_refs, next_component_id = make_frames(
            source_paths, project_dir, next_component_id
        )
        returns = []
        previous_boundary = -1
        for return_data in action_data.get("returns", []):
            boundary = int(return_data["boundaryFrame"])
            # The decoded end frame is a valid final interruption boundary.
            if not previous_boundary < boundary <= len(source_paths) - 1:
                raise ValueError(
                    f"Return boundaries must increase and stay inside the action: {return_data}"
                )
            previous_boundary = boundary
            return_paths = ordered_frames(
                resolve(manifest_path, return_data["framesDir"]), pattern
            )
            if len(return_paths) < 2:
                raise ValueError(f"Return clip needs at least two frames: {return_data}")
            return_fps = float(return_data.get("fps", manifest.get("returnFps", 12)))
            duration_frames = int(
                return_data.get(
                    "durationFrames",
                    round(
                        return_fps
                        * float(
                            return_data.get(
                                "durationSeconds",
                                manifest.get("returnDurationSeconds", 1),
                            )
                        )
                    ),
                )
            )
            if return_fps <= 0 or duration_frames <= 0:
                raise ValueError(f"Invalid return timing: {return_data}")
            return_refs, next_component_id = make_frames(
                return_paths, project_dir, next_component_id
            )
            returns.append(
                ReturnClip(
                    str(return_data["label"]),
                    boundary,
                    return_refs,
                    return_fps,
                    duration_frames,
                )
            )

        if not returns:
            raise ValueError(f"Action has no return clips: {action_data.get('name')}")
        actions.append(Action(str(action_data["name"]), fps, source_refs, returns))
        all_paths.extend(source_paths)
        all_paths.extend(frame.path for clip in returns for frame in clip.frames)

    if not actions:
        raise ValueError("Manifest must contain at least one action")
    size = frame_dimensions(all_paths)
    artboard_size = tuple(int(value) for value in manifest.get("artboardSize", size))
    if len(artboard_size) != 2 or min(artboard_size) <= 0:
        raise ValueError("artboardSize must contain two positive integers")
    return actions, manifest, artboard_size


def animation_block(
    name: str,
    animation_id: int,
    fps: float,
    frames: list[FrameRef],
    duration: int | None = None,
    hold_final_to_duration: bool = False,
) -> list[str]:
    if duration is None:
        duration = len(frames) - 1
    if duration < len(frames) - 1:
        raise ValueError("Animation duration cannot precede the final frame key")
    lines = [
        "    <LinearAnimation fps={} duration={} loopValue=\"oneShot\" name={} id=\"0:{}\">".format(
            quoteattr(number(fps)),
            quoteattr(number(duration)),
            quoteattr(name),
            animation_id,
        ),
        "      <KeyedObject objectId=\"0:200\">",
        "        <KeyedProperty propertyKey=\"296\">",
    ]
    for frame_index, frame in enumerate(frames):
        lines.append(
            "          <KeyFrameId value=\"0:{}\" frame={} interpolationType=\"hold\"/>".format(
                frame.component_id, quoteattr(number(frame_index))
            )
        )
    if hold_final_to_duration and duration > len(frames) - 1:
        lines.append(
            "          <KeyFrameId value=\"0:{}\" frame={} interpolationType=\"hold\"/>".format(
                frames[-1].component_id, quoteattr(number(duration))
            )
        )
    lines.extend(["        </KeyedProperty>", "      </KeyedObject>", "    </LinearAnimation>"])
    return lines


def interrupt_condition(property_path: str) -> list[str]:
    return [
        "          <TransitionViewModelCondition opValue=\"equal\">",
        "            <TransitionPropertyViewModelComparator>",
        "              <BindablePropertyBoolean>",
        f"                <DataBindContext sourcePathIds=\"{property_path}\" propertyKey=\"634\"/>",
        "              </BindablePropertyBoolean>",
        "            </TransitionPropertyViewModelComparator>",
        "            <TransitionValueBooleanComparator value=\"true\"/>",
        "          </TransitionViewModelCondition>",
    ]


def write_interrupt_action(property_path: str, value: bool) -> list[str]:
    literal = "true" if value else "false"
    return [
        "          <ListenerViewModelChange>",
        f"            <BindablePropertyBoolean propertyValue=\"{literal}\">",
        f"              <DataBindContext sourcePathIds=\"{property_path}\" propertyKey=\"634\" direction=\"true\"/>",
        "            </BindablePropertyBoolean>",
        "          </ListenerViewModelChange>",
    ]


def number_condition(property_path: str, value: int) -> list[str]:
    return [
        "          <TransitionViewModelCondition opValue=\"equal\">",
        "            <TransitionPropertyViewModelComparator>",
        "              <BindablePropertyNumber>",
        f"                <DataBindContext sourcePathIds=\"{property_path}\" propertyKey=\"636\"/>",
        "              </BindablePropertyNumber>",
        "            </TransitionPropertyViewModelComparator>",
        f"            <TransitionValueNumberComparator value=\"{value}\"/>",
        "          </TransitionViewModelCondition>",
    ]


def transition(
    state_to_id: int,
    order: int,
    condition: list[str] | None = None,
    action: list[str] | None = None,
    at_exit: bool = True,
) -> list[str]:
    exit_attributes = (
        ' enableExitTime="true" exitTimeIsPercetange="true" exitTime="100"'
        if at_exit
        else ""
    )
    lines = [
        "          <StateTransition stateToId=\"0:{}\" transitionOrder=\"{}\" duration=\"0\"{}>".format(
            state_to_id, order, exit_attributes
        )
    ]
    if condition:
        lines.extend(condition)
    if action:
        lines.extend(action)
    lines.append("          </StateTransition>")
    return lines


def build_rml(
    actions: list[Action],
    manifest: dict,
    artboard_size: tuple[int, int],
    project_dir: Path,
) -> str:
    width, height = artboard_size
    state_machine_id = 2_000_000
    layer_id = 2_000_100
    listener_id = 2_000_200
    hit_target_id = 2_000_300
    hit_style_id = 2_000_301
    view_model_id = 3_000_000
    view_model_instance_id = 3_000_001
    interrupt_property_id = 3_000_002
    next_action_property_id = 3_000_003
    interrupt_property_path = f"0:{view_model_id}-0:{interrupt_property_id}"
    next_action_property_path = f"0:{view_model_id}-0:{next_action_property_id}"

    animation_id = 1_000_000
    state_id = 1_100_000
    action_animations = []
    action_states = []
    return_animations = []
    return_states = []
    router_animations = []
    router_states = []

    for action in actions:
        ends = [clip.boundary_frame for clip in action.returns]
        starts = [0, *ends[:-1]]
        forward_pairs = list(zip(starts, ends))
        forward_ids = []
        forward_state_ids = []
        for start, end in forward_pairs:
            forward_ids.append(animation_id)
            forward_state_ids.append(state_id)
            animation_id += 1
            state_id += 1
        action_animations.append(forward_ids)
        action_states.append(forward_state_ids)

        clip_ids = []
        clip_state_ids = []
        clip_router_animation_ids = []
        clip_router_state_ids = []
        for _ in action.returns:
            clip_ids.append(animation_id)
            clip_state_ids.append(state_id)
            clip_router_animation_ids.append(animation_id + 1)
            clip_router_state_ids.append(state_id + 1)
            animation_id += 1
            state_id += 1
            animation_id += 1
            state_id += 1
        return_animations.append(clip_ids)
        return_states.append(clip_state_ids)
        router_animations.append(clip_router_animation_ids)
        router_states.append(clip_router_state_ids)

    all_frames = [
        frame
        for action in actions
        for frame in ([*action.frames, *(frame for clip in action.returns for frame in clip.frames)])
    ]
    lines = ['<Rive version="1" kind="fragment">', ""]
    for frame in all_frames:
        relative_path = os.path.relpath(frame.path.resolve(), project_dir.resolve()).replace(os.sep, "/")
        lines.append(
            "  <ImageAsset file={} name={} id=\"0:{}\"/>".format(
                quoteattr(relative_path), quoteattr(frame.path.stem), frame.asset_id
            )
        )

    lines.extend(
        [
            "",
            "  <Artboard defaultStateMachineId=\"0:{}\" viewModelId=\"0:{}\" "
            "viewModelInstanceId=\"0:{}\" styleId=\"0:5\" width={} height={} "
            "name={} id=\"0:2\">".format(
                state_machine_id,
                view_model_id,
                view_model_instance_id,
                quoteattr(number(width)),
                quoteattr(number(height)),
                quoteattr(manifest.get("name", "Multi-action frame playback")),
            ),
            "    <LayoutComponentStyle name=\"Artboard Style\" id=\"0:5\"/>",
            "    <LayoutComponentStyle layoutWidthScaleType=\"fixed\" "
            f"layoutHeightScaleType=\"fixed\" name=\"Interrupt Hit Style\" id=\"0:{hit_style_id}\"/>",
            "",
            "    <Solo x={} y={} activeComponentId=\"0:{}\" name=\"Shared Action Frames\" id=\"0:200\">".format(
                quoteattr(number(width / 2)),
                quoteattr(number(height / 2)),
                all_frames[0].component_id,
            ),
        ]
    )
    for frame in all_frames:
        lines.append(
            "      <Image x=\"0\" y=\"0\" assetId=\"0:{}\" name={} id=\"0:{}\"/>".format(
                frame.asset_id, quoteattr(frame.path.stem), frame.component_id
            )
        )
    lines.extend(
        [
            "    </Solo>",
            "",
            "    <LayoutComponent x=\"0\" y=\"0\" width={} height={} styleId=\"0:{}\" "
            "name=\"Interrupt Hit Area\" id=\"0:{}\"/>".format(
                quoteattr(number(width)),
                quoteattr(number(height)),
                hit_style_id,
                hit_target_id,
            ),
            "",
        ]
    )

    for action_index, action in enumerate(actions):
        ends = [clip.boundary_frame for clip in action.returns]
        starts = [0, *ends[:-1]]
        for segment_index, (start, end) in enumerate(zip(starts, ends)):
            lines.extend(
                animation_block(
                    f"{action.name} {start} to {end}",
                    action_animations[action_index][segment_index],
                    action.fps,
                    action.frames[start : end + 1],
                )
            )
        for return_index, clip in enumerate(action.returns):
            lines.extend(
                animation_block(
                    f"{action.name} return {clip.label} to 0",
                    return_animations[action_index][return_index],
                    clip.fps,
                    clip.frames,
                    clip.duration_frames,
                    True,
                )
            )
            lines.extend(
                animation_block(
                    f"{action.name} route after {clip.label}",
                    router_animations[action_index][return_index],
                    1,
                    [clip.frames[-1]],
                )
            )

    lines.extend(
        [
            "",
            f"    <StateMachine name=\"Interruptible Multi-action Playback\" id=\"0:{state_machine_id}\">",
            "      <StateMachineListenerSingle targetId=\"0:{}\" listenerTypeValue=\"click\" "
            "name=\"Request Interrupt\" id=\"0:{}\">".format(hit_target_id, listener_id),
            "        <ListenerViewModelChange>",
        "          <BindablePropertyBoolean propertyValue=\"true\">",
        f"            <DataBindContext sourcePathIds=\"{interrupt_property_path}\" propertyKey=\"634\" direction=\"true\"/>",
        "          </BindablePropertyBoolean>",
            "        </ListenerViewModelChange>",
            "      </StateMachineListenerSingle>",
            f"      <StateMachineLayer name=\"Playback and Return\" id=\"0:{layer_id}\">",
            "        <AnyState x=\"0\" y=\"-120\"/>",
            "        <ExitState x=\"1800\" y=\"-120\"/>",
            "        <EntryState x=\"0\" y=\"0\">",
            f"          <StateTransition stateToId=\"0:{action_states[0][0]}\"/>",
            "        </EntryState>",
        ]
    )

    for action_index, action in enumerate(actions):
        for segment_index in range(len(action.returns)):
            state = action_states[action_index][segment_index]
            animation = action_animations[action_index][segment_index]
            x = 180 + segment_index * 220
            y = action_index * 480
            lines.append(
                "        <AnimationState x={} y={} animationId=\"0:{}\" reset=\"true\" id=\"0:{}\">".format(
                    quoteattr(number(x)), quoteattr(number(y)), animation, state
                )
            )
            lines.extend(
                transition(
                    return_states[action_index][segment_index],
                    0,
                    interrupt_condition(interrupt_property_path),
                    write_interrupt_action(interrupt_property_path, False),
                )
            )
            if segment_index + 1 < len(action.returns):
                next_state = action_states[action_index][segment_index + 1]
            else:
                next_state = action_states[action_index][0]
            lines.extend(transition(next_state, 1))
            lines.append("        </AnimationState>")

        for return_index, clip in enumerate(action.returns):
            state = return_states[action_index][return_index]
            animation = return_animations[action_index][return_index]
            x = 180 + return_index * 220
            y = action_index * 480 + 180
            lines.append(
                "        <AnimationState x={} y={} animationId=\"0:{}\" reset=\"true\" id=\"0:{}\">".format(
                    quoteattr(number(x)), quoteattr(number(y)), animation, state
                )
            )
            lines.extend(
                transition(
                    router_states[action_index][return_index],
                    0,
                    action=write_interrupt_action(interrupt_property_path, False),
                )
            )
            lines.append("        </AnimationState>")

            router_state = router_states[action_index][return_index]
            router_animation = router_animations[action_index][return_index]
            lines.append(
                "        <AnimationState x={} y={} animationId=\"0:{}\" reset=\"true\" id=\"0:{}\">".format(
                    quoteattr(number(x)),
                    quoteattr(number(y + 90)),
                    router_animation,
                    router_state,
                )
            )
            for target_action in range(len(actions)):
                lines.extend(
                    transition(
                        action_states[target_action][0],
                        target_action,
                        number_condition(next_action_property_path, target_action),
                        write_interrupt_action(interrupt_property_path, False),
                        at_exit=False,
                    )
                )
            lines.append("        </AnimationState>")

    lines.extend(
        [
            "      </StateMachineLayer>",
            "    </StateMachine>",
            "  </Artboard>",
            "",
            f"  <ViewModel defaultInstanceId=\"0:{view_model_instance_id}\" name=\"MultiActionPlayback\" id=\"0:{view_model_id}\">",
            f"    <ViewModelPropertyBoolean name=\"interruptRequested\" id=\"0:{interrupt_property_id}\"/>",
            f"    <ViewModelPropertyNumber name=\"nextAction\" id=\"0:{next_action_property_id}\"/>",
            f"    <ViewModelInstance exports=\"true\" name=\"Default\" id=\"0:{view_model_instance_id}\">",
            f"      <ViewModelInstanceBoolean propertyValue=\"false\" viewModelPropertyId=\"0:{interrupt_property_id}\"/>",
            f"      <ViewModelInstanceNumber propertyValue=\"{int(manifest.get('defaultAction', 0))}\" viewModelPropertyId=\"0:{next_action_property_id}\"/>",
            "    </ViewModelInstance>",
            "  </ViewModel>",
            "</Rive>",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    project_dir = args.project_dir.resolve()
    if not (project_dir / "rive.yaml").is_file():
        raise ValueError(f"Not a Rive CLI project: {project_dir}")
    actions, manifest, artboard_size = load_actions(manifest_path)
    output = args.output.resolve() if args.output else project_dir / "scene.rml"
    output.write_text(
        build_rml(actions, manifest, artboard_size, project_dir), encoding="utf-8"
    )

    project_manifest = {
        "name": manifest.get("name"),
        "artboardSize": list(artboard_size),
        "inputs": manifest.get("inputs", []),
        "defaultAction": int(manifest.get("defaultAction", 0)),
        "interruptInput": "interruptRequested",
        "nextActionInput": "nextAction",
        "actions": [
            {
                "name": action.name,
                "fps": action.fps,
                "frameCount": len(action.frames),
                "returns": [
                    {
                        "label": clip.label,
                        "boundaryFrame": clip.boundary_frame,
                        "uniqueFrameCount": len(clip.frames),
                        "keyframeCount": (
                            len(clip.frames) + 1
                            if clip.duration_frames > len(clip.frames) - 1
                            else len(clip.frames)
                        ),
                        "playbackFps": clip.fps,
                        "durationFrames": clip.duration_frames,
                        "durationSeconds": clip.duration_frames / clip.fps,
                    }
                    for clip in action.returns
                ],
            }
            for action in actions
        ],
    }
    (project_dir / "multi_action_manifest.json").write_text(
        json.dumps(project_manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"output={output}")
    print(f"actions={len(actions)}")
    print(
        "frames="
        + ",".join(f"{action.name}:{len(action.frames)}" for action in actions)
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError, KeyError) as error:
        print(f"Error: {error}")
        raise SystemExit(2)
