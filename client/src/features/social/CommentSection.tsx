import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
    useFetchCommentsQuery, useAddCommentMutation,
    useDeleteCommentMutation, useVoteCommentMutation,
    useFetchMyProfileQuery, type CardComment,
} from "./socialApi";
import { useUserInfoQuery } from "../account/accountApi";

// Relative timestamps ("2h ago") keep the thread scannable.
function ago(iso: string) {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
}

type Node = CardComment & { children: Node[] };

function Composer({ game, productId, parentId, onDone, autoFocus }: {
    game: string; productId: number; parentId?: number; onDone?: () => void; autoFocus?: boolean;
}) {
    const [body, setBody] = useState('');
    const [post, { isLoading, error }] = useAddCommentMutation();
    const apiError = (error as { data?: string } | undefined)?.data;

    const submit = async () => {
        if (!body.trim()) return;
        try {
            await post({ game, productId, parentId, body: body.trim() }).unwrap();
            setBody('');
            onDone?.();
        } catch { /* error shows below */ }
    };

    return (
        <div className="comment-composer">
            <textarea className="input comment-composer__box" rows={parentId ? 2 : 3}
                maxLength={2000} autoFocus={autoFocus}
                placeholder={parentId ? 'Write a reply…' : 'Share your take on this card…'}
                value={body} onChange={e => setBody(e.target.value)} />
            {apiError && <p className="comment-error">{String(apiError)}</p>}
            <div className="comment-composer__actions">
                {onDone && <button className="btn btn--outline" onClick={onDone}>Cancel</button>}
                <button className="btn" disabled={isLoading || !body.trim()} onClick={submit}>
                    {parentId ? 'Reply' : 'Comment'}
                </button>
            </div>
        </div>
    );
}

function CommentNode({ node, game, productId, canInteract }: {
    node: Node; game: string; productId: number; canInteract: boolean;
}) {
    const [vote] = useVoteCommentMutation();
    const [del, { isLoading: deleting }] = useDeleteCommentMutation();
    const [replying, setReplying] = useState(false);

    const cast = (value: number) => {
        if (!canInteract) return;
        vote({ id: node.id, value: node.myVote === value ? 0 : value, game, productId });
    };

    return (
        <div className="comment">
            <div className="comment__vote">
                <button className={`comment__arrow${node.myVote === 1 ? ' comment__arrow--up' : ''}`}
                    disabled={!canInteract} onClick={() => cast(1)} aria-label="Upvote">▲</button>
                <span className={`mono comment__score${node.score > 0 ? ' comment__score--up' : node.score < 0 ? ' comment__score--down' : ''}`}>
                    {node.score}
                </span>
                <button className={`comment__arrow${node.myVote === -1 ? ' comment__arrow--down' : ''}`}
                    disabled={!canInteract} onClick={() => cast(-1)} aria-label="Downvote">▼</button>
            </div>
            <div className="comment__main">
                <div className="comment__meta mono">
                    {node.deleted ? (
                        <span className="est-note">[deleted]</span>
                    ) : (
                        <>
                            {node.avatarUrl && <img className="avatar avatar--xs" src={node.avatarUrl} alt="" />}
                            {node.authorPublic
                                ? <Link className="comment__author" to={`/u/${node.author}`}>@{node.author}</Link>
                                : <span className="comment__author">@{node.author}</span>}
                        </>
                    )}
                    <span className="est-note"> · {ago(node.createdAt)}</span>
                </div>
                {!node.deleted && <p className="comment__body">{node.body}</p>}
                <div className="comment__actions">
                    {canInteract && !node.deleted && (
                        <button className="comment__link" onClick={() => setReplying(v => !v)}>Reply</button>
                    )}
                    {node.isMine && !node.deleted && (
                        <button className="comment__link" disabled={deleting}
                            onClick={() => del({ id: node.id, game, productId })}>Delete</button>
                    )}
                </div>
                {replying && (
                    <Composer game={game} productId={productId} parentId={node.id}
                        autoFocus onDone={() => setReplying(false)} />
                )}
                {node.children.length > 0 && (
                    <div className="comment__children">
                        {node.children.map(c => (
                            <CommentNode key={c.id} node={c} game={game} productId={productId}
                                canInteract={canInteract} />
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}

export default function CommentSection({ game, productId }: { game: string; productId: number }) {
    const { data: user } = useUserInfoQuery();
    const { data: profile } = useFetchMyProfileQuery(undefined, { skip: !user });
    const { data: comments } = useFetchCommentsQuery({ game, productId });

    // Flat list -> tree; roots newest-first by score, replies chronological.
    const tree = useMemo(() => {
        const byId = new Map<number, Node>();
        for (const c of comments ?? []) byId.set(c.id, { ...c, children: [] });
        const roots: Node[] = [];
        for (const n of byId.values()) {
            const parent = n.parentId ? byId.get(n.parentId) : undefined;
            if (parent) parent.children.push(n);
            else roots.push(n);
        }
        roots.sort((a, b) => b.score - a.score || b.createdAt.localeCompare(a.createdAt));
        return roots;
    }, [comments]);

    const canComment = !!user && !!profile?.handle;

    return (
        <section className="comments panel detail-panel">
            <h4 className="mono detail-panel__title">
                Comments{comments?.length ? ` (${comments.length})` : ''}
            </h4>
            {!user ? (
                <p className="est-note">
                    <Link to="/login">Sign in</Link> to join the discussion.
                </p>
            ) : !profile?.handle ? (
                <p className="est-note">
                    <Link to="/settings/profile">Set a username</Link> to join the discussion.
                </p>
            ) : (
                <Composer game={game} productId={productId} />
            )}
            {tree.length === 0
                ? <p className="est-note">No comments yet. Be the first.</p>
                : tree.map(n => (
                    <CommentNode key={n.id} node={n} game={game} productId={productId}
                        canInteract={canComment} />
                ))}
        </section>
    );
}
