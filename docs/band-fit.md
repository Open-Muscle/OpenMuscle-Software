# Band fit and placement

How well a model works depends more on **putting the band in the same place every time** than on almost anything else. The band measures pressure from the muscles under it. If it slides a centimetre between recordings, the model is looking at different muscles and the score drops. This page shows how to put the band on, and how to put it back the same way next time.

## The short version

1. Sensor side against the bare skin, never over a sleeve.
2. The long side of the band runs **along** the forearm, not around it.
3. Place it on the thicker, muscular part of the forearm, between the elbow and the middle of the forearm.
4. Snug, not tight: you can slide one finger under the strap, and you can still wiggle your fingers freely.
5. Mark the position once, and put the band on the same way every time.
6. Do the squeeze test (below) before every recording session.

<!-- Maintainer note: annotated photos of band position, orientation, strap tension and the
     optional finger labeler hold are still to be added, and the placement wording should be
     confirmed by the hardware owner before release. Put the images in docs/images/ and link
     them under the matching headings. -->

## Which way round

- The sensing surface (the flexible side with the grid of sensing cells) goes against your skin.
- The circuit board with the screen, buttons and USB-C port faces away from your skin, so you can read the screen and reach the buttons.
- The band's long axis lines up with your forearm, from the elbow toward the wrist.
- Put the **left** band on the left forearm and the **right** band on the right forearm. In the Studio you tell it which band is which with the **role** menu on each band's card, so wear the one you called left on your left arm every time.

## Where on the forearm

- Look for the part of the forearm that bulges when you make a fist and open it again. Feel it with your other hand. That is where the muscles that move the fingers are.
- A good starting point is the upper half of the forearm, a hand-width or two below the elbow crease, centred on that bulge. Avoid the bony part right at the wrist and the soft joint at the elbow.
- Keep the band on bare skin. Push up a sleeve; do not wear the band over fabric.

The exact spot matters less than doing it the same way each time. Pick a spot, then keep it.

## How tight

- Fasten the strap so the band lies flat against the skin and does not slide when you move your arm and wrist.
- You should be able to slip one finger under the strap. If it leaves a deep mark after ten minutes, or your hand tingles or goes cold, loosen it.
- Too loose is the more common mistake: the cells at the edges stay dark in the squeeze test and the model scores low.
- Tightness changes the numbers. Try to fasten it to the same snugness each time.

## Put it back in the same place

- With a skin-safe pen, make two small dots on your forearm just outside the band's edge, or note the distance from the elbow crease (for example "three finger-widths below the crease, centred on the bulge"). Wipe the dots off when you are done.
- Or take a photo of your arm with the band on and look at it when you put it on next time.
- If you take the band off between recordings, put it back the same way before you record again. If you notice it has slipped during a recording, stop, fix it, and start a new recording.
- Keep to one band position within one training. If you move the band, record new takes in the new position and train on those; do not mix them with takes from the old position.

## The squeeze test

Do this once the band is on and showing in the Studio (see [getting-started.md](getting-started.md), step 5).

1. Look at the colourful grid for your band. With your arm relaxed, most of it should be dark or a quiet colour.
2. Press firmly with a fingertip on the outside of the band, then slowly make a fist.
3. **You should see** the grid light up in warm colours (pink, orange, yellow) where you press, and glow more as you squeeze harder. Relax, and it should fade again.
4. If a whole region never changes colour, the band is not touching the skin there: refasten it, then press again.
5. If the whole grid is already bright with your arm relaxed, the band is too tight. Loosen it a little.

If the grid stays flat whatever you do, see [troubleshooting.md](troubleshooting.md#the-heatmap-is-flat-or-stuck).

## Two bands, one hand held for the camera

If you wear a band on each arm and use the camera as the labeler, the camera can only watch one hand at a time. Using one hand to label both bands is called **mirrored** training: you move both hands together, making the same movement with each, while the camera watches one of them. It works when both hands do the same thing at the same moment. See [webcam-labeling.md](webcam-labeling.md#mirrored-training) for how to set it up in the Studio.

If you use the optional LASK5 finger labeler, hold it in the hand of the banded arm, with your thumb on the joystick and one finger on each of its four pistons (see the LASK5 repository for photos), and keep the rest of the arm relaxed, because a tense forearm changes the readings.

## Quick checklist before every recording session

- Band on bare skin, sensor side in, long side along the forearm.
- Same place as last time (dots or photo).
- Snug: one finger fits under the strap.
- Squeeze test: the grid reacts where you press.
- The right band on the right arm; the Studio roles match.
- Arm relaxed on a table or in your lap, not hanging, unless the movement you are recording needs it.
