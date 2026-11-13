import mido
import obspython as obs
import json
import time

midi_device = ""
device = None
start_note = 0

# Manual lists, used only when "Detect automatically" is off.
audio_source = []
scene_sources = []
opacity_sources = []

# With "Detect automatically" on (the default), the script reads OBS itself:
#   audio  -> the global audio devices (Desktop Audio, Mic/Aux...) -> first knobs
#   video  -> every video source used in your scenes               -> remaining knobs
#   scenes -> all scenes, in the order of the Scenes dock           -> pads
auto_detect = True
KNOB_COUNT = 8
AUTO_REFRESH_SECONDS = 2.0
_detected = {"time": 0.0, "audio": [], "video": [], "scenes": []}

# Knobs after the audio ones fade video sources through a Color Correction
# filter with this name, added to the source the first time its knob moves.
OPACITY_FILTER = "MPK Opacity"
# Sources this script hid (knob at 0), so turning the knob up only re-shows
# what the knob hid and never a source you hid by hand.
hidden_by_knob = set()
# Knob/controller CC numbers already reported as unmapped in the script log.
unmapped_logged = set()


# --- Detection -------------------------------------------------------------

def detect_scenes():
    return list(obs.obs_frontend_get_scene_names() or [])

def detect_audio():
    # Global audio channels 1-6: Desktop Audio 1/2, Mic/Aux 1-4.
    names = []
    for channel in range(1, 7):
        source = obs.obs_get_output_source(channel)
        if source is not None:
            if obs.obs_source_get_output_flags(source) & obs.OBS_SOURCE_AUDIO:
                names.append(obs.obs_source_get_name(source))
            obs.obs_source_release(source)
    return names

def detect_video(scene_names):
    # Video inputs in scene order, top of each scene's Sources list first.
    # Nested scenes and groups are skipped; each source is listed once.
    names = []
    for scene_name in scene_names:
        scene_source = obs.obs_get_source_by_name(scene_name)
        if scene_source is None:
            continue
        items = obs.obs_scene_enum_items(obs.obs_scene_from_source(scene_source)) or []
        for item in reversed(items):
            source = obs.obs_sceneitem_get_source(item)
            name = obs.obs_source_get_name(source)
            if (obs.obs_source_get_type(source) == obs.OBS_SOURCE_TYPE_INPUT
                    and obs.obs_source_get_output_flags(source) & obs.OBS_SOURCE_VIDEO
                    and name not in names):
                names.append(name)
        obs.sceneitem_list_release(items)
        obs.obs_source_release(scene_source)
    return names

def refresh_detection(quiet=False):
    scenes = detect_scenes()
    audio = detect_audio()[:KNOB_COUNT]
    video = detect_video(scenes)[:KNOB_COUNT - len(audio)]
    changed = (scenes, audio, video) != (_detected["scenes"], _detected["audio"], _detected["video"])
    _detected.update(time=time.monotonic(), scenes=scenes, audio=audio, video=video)
    if changed and not quiet:
        print("Mapping changed:\n" + mapping_text())

def mappings():
    """(scenes, audio, video) currently in use."""
    if not auto_detect:
        return scene_sources, audio_source, opacity_sources[:max(0, KNOB_COUNT - len(audio_source))]
    if time.monotonic() - _detected["time"] > AUTO_REFRESH_SECONDS:
        refresh_detection()
    return _detected["scenes"], _detected["audio"], _detected["video"]

def mapping_text():
    scenes, audio, video = mappings() if not auto_detect else (_detected["scenes"], _detected["audio"], _detected["video"])
    knobs = ["K%d volume: %s" % (i + 1, n) for i, n in enumerate(audio)]
    knobs += ["K%d opacity: %s" % (len(audio) + i + 1, n) for i, n in enumerate(video)]
    pads = ["%d: %s" % (start_note + i, n) for i, n in enumerate(scenes)]
    return "Knobs\n  " + ("\n  ".join(knobs) or "(none)") + "\nPads (note: scene)\n  " + ("\n  ".join(pads) or "(none)")

def on_frontend_event(event):
    if event in (obs.OBS_FRONTEND_EVENT_FINISHED_LOADING,
                 obs.OBS_FRONTEND_EVENT_SCENE_LIST_CHANGED,
                 obs.OBS_FRONTEND_EVENT_SCENE_COLLECTION_CHANGED):
        _detected["time"] = 0.0  # re-detect on the next MIDI message


# --- MIDI ------------------------------------------------------------------

# The script connects by itself: on load, when the selected device changes,
# and every few seconds while the MPK is missing (not plugged in yet, or
# unplugged). Pressing Disconnect pauses this until Connect is pressed.
RECONNECT_MS = 3000
user_disconnected = False

def pick_device(names):
    if midi_device in names:
        return midi_device
    # Windows numbers ports ("MPK Mini Mk II 0", "... 1"), and the number can
    # change between sessions, so fall back to any MPK that is present.
    for name in names:
        if "mpk" in name.lower():
            return name
    return None

def ensure_connected():
    global device
    if user_disconnected:
        return
    names = mido.get_input_names()
    if device is not None:
        if device.name in names:
            return
        print("MIDI device '%s' is gone, waiting for it to come back" % device.name)
        close_device()
    name = pick_device(names)
    if name is None:
        return
    try:
        device = mido.open_input(name, callback=midi_update)
        print("Connected to '%s'" % name)
    except Exception as e:  # e.g. the port is busy in another program
        device = None
        print("Could not open '%s': %s" % (name, e))

def close_device():
    global device
    # Always close the previous port before opening another: an extra open
    # port made every pad press switch the scene once per port.
    if device is not None:
        device.callback = None
        device.close()
        device = None

def connect_to_device(a=None, b=None):
    global user_disconnected
    user_disconnected = False
    close_device()
    ensure_connected()

def disconnect_from_device(a=None, b=None):
    global user_disconnected
    user_disconnected = True
    if device is not None:
        print("Disconnected from '%s'" % device.name)
    close_device()

def current_scene_name():
    current = obs.obs_frontend_get_current_scene()
    if current is None:
        return None
    name = obs.obs_source_get_name(current)
    obs.obs_source_release(current)
    return name

def set_source_visible(name, visible):
    # Show/hide the source in every scene that contains it.
    if visible and name not in hidden_by_knob:
        return
    if not visible and name in hidden_by_knob:
        return
    scenes = obs.obs_frontend_get_scenes()
    for scene_source in scenes:
        scene = obs.obs_scene_from_source(scene_source)
        item = obs.obs_scene_find_source(scene, name)
        if item is not None:
            obs.obs_sceneitem_set_visible(item, visible)
    obs.source_list_release(scenes)
    if visible:
        hidden_by_knob.discard(name)
    else:
        hidden_by_knob.add(name)

def set_source_opacity(name, value):
    source = obs.obs_get_source_by_name(name)
    if source is None:
        return
    settings = obs.obs_data_create()
    obs.obs_data_set_double(settings, "opacity", value / 127.0)  # color_filter_v2: 0.0-1.0
    opacity_filter = obs.obs_source_get_filter_by_name(source, OPACITY_FILTER)
    if opacity_filter is None:
        opacity_filter = obs.obs_source_create_private("color_filter_v2", OPACITY_FILTER, settings)
        obs.obs_source_filter_add(source, opacity_filter)
    else:
        obs.obs_source_update(opacity_filter, settings)
    obs.obs_source_release(opacity_filter)
    obs.obs_data_release(settings)
    obs.obs_source_release(source)
    # Knob all the way down hides the source; any other position shows it.
    set_source_visible(name, value > 0)

def midi_update(msg):
    scenes, audio, video = mappings()
    if (msg.type == "control_change"):
        # First knobs -> audio volume, the following knobs -> video opacity.
        index = msg.control - 1
        if 0 <= index < len(audio):
            source = obs.obs_get_source_by_name(audio[index])
            if source is not None:
                obs.obs_source_set_volume(source, float(msg.value) / 127.0)
                obs.obs_source_release(source)
        elif 0 <= index - len(audio) < len(video):
            set_source_opacity(video[index - len(audio)], msg.value)
        elif msg.control not in unmapped_logged:
            # Logged once per control so the script log shows what the MPK sends.
            unmapped_logged.add(msg.control)
            print("CC %d (channel %d) has nothing mapped: %d volume knob(s), %d opacity knob(s)"
                  % (msg.control, msg.channel + 1, len(audio), len(video)))
    elif (msg.type == "note_on" and msg.velocity > 0):  # velocity 0 is a note release
        index = msg.note - start_note
        if 0 <= index < len(scenes):
            # Already on this scene: do nothing, so the transition isn't restarted.
            if scenes[index] == current_scene_name():
                return
            scene = obs.obs_get_source_by_name(scenes[index])
            if scene is not None:
                obs.obs_frontend_set_current_scene(scene)
                obs.obs_source_release(scene)


# --- OBS script hooks --------------------------------------------------------

def script_description():
    return ("Control OBS with an AKAI MPK Mini.\n"
            "Knobs: volume of your audio devices, then opacity of your video sources "
            "(all the way down hides it). Pads: switch scenes.")

def script_load(settings):
    obs.obs_frontend_add_event_callback(on_frontend_event)
    ensure_connected()
    obs.timer_add(ensure_connected, RECONNECT_MS)

def script_unload():
    obs.timer_remove(ensure_connected)
    close_device()

def refresh_clicked(props, prop):
    refresh_detection()
    obs.obs_property_set_description(obs.obs_properties_get(props, "mapping"), mapping_text())
    return True

def script_properties():
    props = obs.obs_properties_create()
    p = obs.obs_properties_add_list(props, "source", "midi source",
                                    obs.OBS_COMBO_TYPE_EDITABLE,
                                    obs.OBS_COMBO_FORMAT_STRING)
    inputs = mido.get_input_names()
    if inputs is not None:
        for midi in inputs:
            obs.obs_property_list_add_string(p, midi, midi)

    obs.obs_properties_add_button(props, "connect", "Connect", connect_to_device)
    obs.obs_properties_add_button(props, "disconnect", "Disconnect", disconnect_from_device)
    obs.obs_properties_add_int(props, "start-note", "Scene select start note", 0, 108, 1)

    if auto_detect:
        refresh_detection()
    obs.obs_properties_add_text(props, "mapping", mapping_text(), obs.OBS_TEXT_INFO)
    obs.obs_properties_add_button(props, "refresh", "Refresh detected mapping", refresh_clicked)

    # Manual override: unticked (the default) = automatic detection.
    obs.obs_properties_add_bool(props, "manual-mapping", "Use the manual lists below instead of automatic detection")
    obs.obs_properties_add_text(props, "audio-sources", "Audio Sources (manual)", obs.OBS_TEXT_MULTILINE)
    obs.obs_properties_add_text(props, "opacity-sources", "Opacity Sources (manual)", obs.OBS_TEXT_MULTILINE)
    obs.obs_properties_add_text(props, "scene-sources", "Scene Sources (manual)", obs.OBS_TEXT_MULTILINE)
    return props

def value_is_json(json_value):
    try:
        obj = json.loads(json_value)
    except ValueError:
        return False
    return isinstance(obj, list)

def script_update(settings):
    global midi_device
    global audio_source
    global scene_sources
    global opacity_sources
    global start_note
    global auto_detect
    selected = obs.obs_data_get_string(settings, "source")
    if selected != midi_device:
        midi_device = selected
        # A different MPK/port was picked: drop the current one, the reconnect
        # timer (or the call below) opens the new choice.
        if device is not None and device.name != midi_device and midi_device in mido.get_input_names():
            close_device()
            ensure_connected()
    start_note = obs.obs_data_get_int(settings, "start-note")
    # Automatic unless the manual box is ticked (an unset bool reads as False,
    # so this works without relying on script_defaults).
    auto_detect = not obs.obs_data_get_bool(settings, "manual-mapping")
    _detected["time"] = 0.0
    audio_sources_text = obs.obs_data_get_string(settings, "audio-sources")
    if value_is_json(audio_sources_text):
        audio_source = json.loads(audio_sources_text)
    scene_sources_text = obs.obs_data_get_string(settings, "scene-sources")
    if value_is_json(scene_sources_text):
        scene_sources = json.loads(scene_sources_text)
    opacity_sources_text = obs.obs_data_get_string(settings, "opacity-sources")
    if value_is_json(opacity_sources_text):
        opacity_sources = json.loads(opacity_sources_text)
    if auto_detect:
        refresh_detection(quiet=True)
    print(("Automatic mapping:\n" if auto_detect else "Manual mapping:\n") + mapping_text())
