import { useRef } from "react";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import { setSearchTerm } from "./catalogSlice";
import { useDebouncedSearch } from "../../lib/useDebouncedSearch";
import { GAME_LABEL } from "../../lib/games";

// Photo/image search is built but hidden for now: ImageSearchModal, the
// cards/image-search endpoint, and the useImageSearchMutation hook all still
// exist — re-enable by restoring the camera button + modal here.
//
// One field, one source of truth: wherever Filters renders it (the desktop
// rail, or the bar above the results on tablet/mobile and in row view) it
// reads and writes catalog.searchTerm, so the URL, the list and the field
// always agree.
export default function Search() {
    const { searchTerm, game } = useAppSelector(state => state.catalog);
    const dispatch = useAppDispatch();
    const inputRef = useRef<HTMLInputElement>(null);
    const { term, onChange, commit } = useDebouncedSearch(searchTerm ?? '',
        v => dispatch(setSearchTerm(v)));

    // Enter (the keyboard's Search key on a phone) searches now instead of
    // after the typing pause. On touch screens it also drops the keyboard,
    // which otherwise covers the results it just asked for; a desktop keeps
    // its focus for the next tweak.
    const submit = (e: React.FormEvent) => {
        e.preventDefault();
        commit(term);
        if (window.matchMedia('(hover: none) and (pointer: coarse)').matches)
            inputRef.current?.blur();
    };

    return (
        <form className="field search-field" role="search" onSubmit={submit}>
            <label htmlFor="search">Search cards</label>
            <div className={`search-field__box${term ? ' search-field__box--filled' : ''}`}>
                {/* The API matches part of a card's name within one game, so
                    the placeholder names the game. No autocorrect: it "fixes"
                    card names (Zoro -> Zero). */}
                <input
                    ref={inputRef}
                    id="search"
                    className="input search-field__input"
                    type="search"
                    enterKeyHint="search"
                    autoComplete="off"
                    autoCorrect="off"
                    spellCheck={false}
                    placeholder={GAME_LABEL[game] ? `Search ${GAME_LABEL[game]} cards` : 'Search cards'}
                    value={term}
                    onChange={e => onChange(e.target.value)}
                />
                {term && (
                    // Clears at once, no typing pause. Mouse-down doesn't take
                    // the focus: mid-typing the keyboard stays up for the next
                    // name, and clearing a finished search doesn't open it.
                    <button type="button" className="search-field__clear"
                        aria-label="Clear search" title="Clear search"
                        onMouseDown={e => e.preventDefault()}
                        onClick={() => commit('')}>
                        ✕
                    </button>
                )}
            </div>
        </form>
    )
}
