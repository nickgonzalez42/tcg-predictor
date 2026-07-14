import { decrement, increment } from "./counterReducer";
import { useAppSelector, useAppDispatch } from "../../app/store/store";
import { usePageMeta } from "../../lib/usePageMeta";

export default function ContactPage() {
    usePageMeta("Contact", "Get in touch with TCG Predictor.");
  const { data } = useAppSelector(state => state.counter);
  const dispatch = useAppDispatch();
  return (
    <div>
      <p>Contact page</p>
      <p>Data is: {data}</p>
      <div className="btn-group">
        <button className="btn" onClick={() => dispatch(decrement(1))}>Decrement</button>
        <button className="btn" onClick={() => dispatch(increment(1))}>Increment</button>
        <button className="btn" onClick={() => dispatch(increment(5))}>Increment 5</button>
      </div>
    </div>
  );
}
