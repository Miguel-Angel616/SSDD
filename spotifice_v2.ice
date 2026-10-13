[["underscore"]]
#include <Ice/Identity.ice>

module Spotifice {
    class TrackInfo {
        string id;
        string title;
    };

    sequence<byte> AudioChunk;
    sequence<TrackInfo> TrackInfoSeq;

    exception Error {
        optional(1) string item;
        string reason;
    };

    exception IOError extends Error{};
    exception BadIdentity extends Error{};
    exception BadReference extends Error{};
    exception PlayerError extends Error{};
    exception StreamError extends Error{};
    exception TrackError extends Error{};
    exception PlaylistError extends Error{};  // new in version 2

    interface MusicLibrary {
        idempotent TrackInfoSeq get_all_tracks() throws IOError;
        idempotent TrackInfo get_track_info(string track_id) throws IOError, TrackError;
    };

    interface StreamManager {
        idempotent void open_stream(Ice::Identity media_render_id, string track_id)
            throws BadIdentity, IOError, TrackError;
        idempotent void close_stream(Ice::Identity media_render_id);
        AudioChunk get_chunk(Ice::Identity media_render_id, int chunk_size)
            throws IOError, StreamError;
    };
