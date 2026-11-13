import mido
import obspython as obs
import json

midi_device = ""
device = None
audio_source = []
scene_sources = []
opacity_sources = []
start_note = 0

# Knobs after the audio ones fade video sources through a Color Correction
# filter with this name, added to the source the first time its knob moves.
OPACITY_FILTER = "MPK Opacity"
# Sources this script hid (knob at 0), so turning the knob up only re-shows
# what the knob hid and never a source you hid by hand.
hidden_by_knob = set()

def connect_to_device(a, b):
    global device
    global midi_device
    # Close any previous connection first. Clicking Connect again used to open
    # an extra port each time, so every pad press switched the scene once per
    # open port and restarted the transition.
    disconnect_from_device(None, None)
    device = mido.open_input(midi_device, callback=midi_update)

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
    global audio_source
    global start_note
    global scene_sources
    if (msg.type == "control_change"):
        # K1..Kn -> audio volume (one per Audio Sources entry),
        # the following knobs -> opacity of the Opacity Sources entries.
        index = msg.control - 1
        if 0 <= index < len(audio_source):
            audio = obs.obs_get_source_by_name(audio_source[index])
            if audio is not None:
                volume = float(msg.value) / 127.0
                obs.obs_source_set_volume(audio, volume)
                obs.obs_source_release(audio)
        elif 0 <= index - len(audio_source) < len(opacity_sources):
            set_source_opacity(opacity_sources[index - len(audio_source)], msg.value)
    elif (msg.type == "note_on" and msg.velocity > 0):  # velocity 0 is a note release
        index = msg.note - start_note
        if (index < len(scene_sources) and index >= 0):
            # Already on this scene: do nothing, so the transition isn't restarted.
            if scene_sources[index] == current_scene_name():
                return
            scene = obs.obs_get_source_by_name(scene_sources[index])
            if scene is not None:
                obs.obs_frontend_set_current_scene(scene)
                obs.obs_source_release(scene)



def script_unload():
    disconnect_from_device(None, None)

def disconnect_from_device(a, b):
    global device
    global midi_device
    print("attempting disconnect")
    if device is not None:
        device.close()
        device.callback = None
        device = None

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
    obs.obs_properties_add_text(props, "audio-sources", "Audio Sources", obs.OBS_TEXT_MULTILINE)
    obs.obs_properties_add_text(props, "scene-sources", "Scene Sources", obs.OBS_TEXT_MULTILINE)
    obs.obs_properties_add_text(props, "opacity-sources", "Opacity Sources (knobs after audio)", obs.OBS_TEXT_MULTILINE)

    obs.obs_properties_add_int(props, "start-note", "Scene select start note", 0, 108, 1) 
    return props

def value_is_json(json_value):
    try:
        obj = json.loads(json_value)
    except ValueError:
        return False
    return True

def script_update(settings):
    global midi_device
    global audio_source
    global scene_sources
    global opacity_sources
    global start_note
    midi_device = obs.obs_data_get_string(settings, "source")
    start_note = obs.obs_data_get_int(settings, "start-note")
    audio_sources_text = obs.obs_data_get_string(settings, "audio-sources")
    if value_is_json(audio_sources_text):
        audio_source = json.loads(audio_sources_text)
    scene_sources_text = obs.obs_data_get_string(settings, "scene-sources")
    if value_is_json(scene_sources_text):
        scene_sources = json.loads(scene_sources_text)
    opacity_sources_text = obs.obs_data_get_string(settings, "opacity-sources")
    if value_is_json(opacity_sources_text):
        opacity_sources = json.loads(opacity_sources_text)